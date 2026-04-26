# AI-Codereview-Gitlab 使用与维护指南

## 1. 项目简介

AI-Codereview-Gitlab 是一个基于大模型（如 DeepSeek、OpenAI 等）的 GitLab 自动代码审查工具，支持：

- Merge Request 自动评审
- `@ai` 触发手动评审
- `@ai + 文本` 对话式回复
- 低分时创建阻止合并的未解决讨论
- 钉钉 / 企业微信 / 飞书消息推送
- 日报生成
- Dashboard 可视化统计
- Docker 部署

---

## 2. 核心能力说明

### 2.1 Merge Request 自动评审
当 GitLab 触发 MR 相关 webhook 时，系统会自动拉取 MR 变更并进行 AI 评审。

当前支持：
- 首次 MR：全量评审
- 后续更新 MR：优先增量评审
- 无新增 commit：不重复全量评审

---

### 2.2 `@ai` 手动触发评审
在 MR 评论中仅输入 `@ai`（或等效机器人 mention）时，系统会触发标准代码评审。

行为：
- 评论开始处理时显示 `eyes`
- 评审完成后移除 `eyes`
- 若得分高于 90，添加 `tada`
- 若得分低于 60，添加 `cold_sweat`

---

### 2.3 `@ai + 文本` 对话模式
在 MR 评论中输入：

```text
@ai 请解释一下这个改动的风险
```

系统不会输出标准评审报告，而是根据：
- 用户附加文本
- 当前 MR / diff / 评论上下文

生成一条对话式回复。

此路径不参与评分，也不触发低分阻止合并逻辑。

---

### 2.4 低分阻止合并
如果 MR 标准评审得分低于阈值，系统会自动创建一个 **未解决讨论（unresolved discussion）**，用于阻止合并。

前提：
- GitLab 项目已开启“所有讨论解决后才允许合并”

当前规则：
- 功能关闭：不创建阻塞讨论
- 评分低于阈值：创建阻塞讨论
- 已存在未解决的低分阻塞讨论：不重复创建

---

### 2.5 审核交互反馈
为了提升可见性，系统增加了表情状态反馈：

#### `@ai` 触发路径
- 开始：`eyes`
- 结束后：
  - 高分：`tada`
  - 低分：`cold_sweat`

#### MR 自动审核路径
- 先新增一条占位回复
- 在该回复上添加 `eyes`
- 审核完成后将该回复编辑为正式审核结果
- 再根据得分添加 `tada` 或 `cold_sweat`

---

## 3. Webhook 配置建议

### 3.1 必要事件
如需完整支持 MR 审核与 `@ai` 交互，建议至少启用：

- **Merge Request events**
- **Comment events / Note Hook**

如仍需支持直接 push 审查，可额外启用：

- **Push events**

---

### 3.2 说明
- **Merge Request events**：用于 MR 创建、更新、合并等流程
- **Comment events / Note Hook**：用于识别 MR 评论中的 `@ai`
- **Push events**：用于直接推送代码时触发审查

---

### 3.3 避免重复触发的建议
系统已增加一定的去重和增量能力，但仍建议遵循以下原则：

- MR 审核优先于分支 push 审核
- 已有 open MR 的 source branch，尽量避免重复走 push review
- 同一个 MR 的相同 `last_commit_id` 不重复审核
- 相同 note/comment 不重复回复

---

## 4. 增量评审机制说明

### 4.1 目标
避免同一个 MR 每次新增 commit 或重复 `@ai` 时，都重新评审全部历史变更。

---

### 4.2 当前行为
#### 首次评审
- 全量评审
- 记录当前 `last_commit_id`

#### 后续评审
- 若最新 `last_commit_id` 与上次一致：
  - 不重复评审
  - `@ai` 路径会提示“无新增改动”
- 若不同：
  - 只评审上次审核后新增的 diff
- 若增量 diff 获取失败：
  - 回退到全量评审

---

### 4.3 边界情况
以下情况可能回退为全量评审：
- force push
- rebase
- compare API 失败
- 上次审核 commit 已不在当前提交链中

---

## 5. 低分阻止合并配置

在配置文件中可设置：

```env
LOW_SCORE_BLOCK_MR_ENABLED=0
LOW_SCORE_BLOCK_MR_THRESHOLD=60
```

### 参数说明
- `LOW_SCORE_BLOCK_MR_ENABLED`
  - `0`：关闭
  - `1`：开启

- `LOW_SCORE_BLOCK_MR_THRESHOLD`
  - 当评审总分 **小于** 该值时，创建未解决讨论阻止合并

### 示例
```env
LOW_SCORE_BLOCK_MR_ENABLED=1
LOW_SCORE_BLOCK_MR_THRESHOLD=60
```

---

## 6. 交互表情规则

### 6.1 `@ai` 评审路径
- 开始评审：`eyes`
- 评审完成：
  - `score > 90`：`tada`
  - `score < 60`：`cold_sweat`

### 6.2 MR 自动审核路径
- 创建占位回复
- 占位回复添加 `eyes`
- 审核完成后编辑为正式结果
- 然后根据评分添加：
  - `tada`
  - `cold_sweat`

### 6.3 说明
- 表情只作为交互反馈，不影响主流程
- 表情添加/删除失败时，仅记录日志，不影响审核结果发送

---

## 7. 常用配置项说明

以下仅列出与评审相关的重点配置，具体以项目配置文件为准。

### 7.1 Dashboard 登录配置
```env
DASHBOARD_USER=admin
DASHBOARD_PASSWORD=admin
DASHBOARD_SECRET_KEY=your-secret-key
```

### 7.2 Push 审核开关
```env
PUSH_REVIEW_ENABLED=0
```

### 7.3 草稿 MR 是否审核
如项目已支持：
```env
DRAFT_MR_REVIEW_ENABLED=0
```

### 7.4 低分阻止合并
```env
LOW_SCORE_BLOCK_MR_ENABLED=0
LOW_SCORE_BLOCK_MR_THRESHOLD=60
```

---

## 8. 日常使用说明

### 8.1 自动评审
开发者创建或更新 MR 后，系统自动触发评审，无需额外操作。

---

### 8.2 手动触发评审
在 MR 评论中输入：

```text
@ai
```

用于手动触发标准评审。

---

### 8.3 对话式提问
在 MR 评论中输入：

```text
@ai 这段改动是否会影响并发安全？
```

系统将按对话模式回复，而不是输出标准评审报告。

---

## 9. 维护建议

### 9.1 配置变更后重启服务
修改 `.env` 或 webhook 配置后，建议重启服务，确保新配置生效。

如使用 Docker：
```bash
docker compose down
docker compose up -d --build
```

---

### 9.2 定期检查 webhook 是否正常投递
重点确认：
- Merge Request event 是否正常触发
- Comment event / Note Hook 是否启用
- 请求是否返回 2xx
- 是否存在重复投递或失败重试

---

### 9.3 定期检查数据库
统计、增量评审、日志查询依赖本��数据库。建议定期确认：
- 数据库文件是否存在
- 表结构是否完整
- 是否有异常增长或写入失败

可重点检查：
- `mr_review_log`
- `push_review_log`

---

### 9.4 关注 GitLab API 兼容性
以下功能依赖 GitLab API：
- MR diff 拉取
- compare 增量比较
- 评论发送 / 编辑
- discussion 查询 / 创建
- emoji / award reaction

升级 GitLab 版本后，建议做一次回归验证。

---

## 10. 常见问题排查

### 10.1 MR 评论中 `@ai` 没反应
优先检查：
1. 是否启用了 **Comment events / Note Hook**
2. webhook 是否成功送达
3. 是否是 `@ai + 文本` 路径被识别为聊天模式
4. 服务端日志中是否有 note 事件处理异常

---

### 10.2 同一个 MR 重复审核
优先检查：
1. 当前是否已有新增 commit
2. `last_commit_id` 是否正确记录
3. 是否发生 force push / rebase 导致回退全量
4. 是否同时启用了 push 与 MR 事件，造成双重触发

---

### 10.3 低分没有阻止合并
优先检查：
1. `LOW_SCORE_BLOCK_MR_ENABLED` 是否开启
2. `LOW_SCORE_BLOCK_MR_THRESHOLD` 是否正确配置
3. 评审结果是否成功解析出分数
4. GitLab 项目是否开启“所有讨论解决后才允许合并”

---

### 10.4 Dashboard 没有统计数据
优先检查：
1. 最近是否真的有评审记录写入数据库
2. 时间筛选范围是否覆盖已有数据
3. 数据库是否迁移完成
4. 服务是否使用了最新代码并正确重启

---

### 10.5 表情没有出现或没有被移除
优先检查：
1. GitLab API token 权限是否足够
2. 目标 note/comment 是否可操作
3. award emoji API 是否成功返回
4. 日志中是否有添加/删除 emoji 失败记录

---

## 11. 推荐运维策略

### 推荐配置思路
- **MR review**：开启
- **Comment event / Note Hook**：开启
- **Push review**：按团队流程决定是否开启
- **低分阻止合并**：建议在团队形成稳定评分标准后开启
- **自动审核表情反馈**：建议开启，提高可见性

---

## 12. 后续可扩展方向
当前系统已经支持基础自动化评审，后续可考虑继续扩展：

- 低分阻塞主题自动追加最新评分回复
- 评分恢复后自动提示可手动解除阻塞
- `@ai full review` 全量重审命令
- 历史问题去重与持续跟踪
- 更细粒度的风险分类与标签化

---

## 13. 维护原则
项目后续维护建议遵循以下原则：

1. **优先保证主审核流程稳定**
2. **交互增强不得影响评审主链路**
3. **所有 GitLab API 的附加能力（表情、discussion、编辑评论）都要做错误隔离**
4. **增量审核失败时优先回退全量，不要中断服务**
5. **配置项尽量显式、可关闭、默认安全**

---

## 14. 版本更新建议
每次新增功能后，建议至少验证以下场景：

- MR 自动审核
- MR 增量审核
- `@ai` 仅 mention 标准评审
- `@ai + 文本` 对话模式
- 低分阻止合并
- 表情反馈是否正确加/删
- Dashboard 数据是否正常展示

---
