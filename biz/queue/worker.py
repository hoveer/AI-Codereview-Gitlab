import os
import traceback
from datetime import datetime

from biz.entity.review_entity import MergeRequestReviewEntity, PushReviewEntity
from biz.event.event_manager import event_manager
from biz.platforms.gitlab.webhook_handler import filter_changes, MergeRequestHandler, PushHandler, NoteHandler, parse_bot_mention
from biz.platforms.github.webhook_handler import filter_changes as filter_github_changes, PullRequestHandler as GithubPullRequestHandler, PushHandler as GithubPushHandler
from biz.platforms.gitea.webhook_handler import filter_changes as filter_gitea_changes, PullRequestHandler as GiteaPullRequestHandler, \
    PushHandler as GiteaPushHandler
from biz.service.review_service import ReviewService
from biz.utils.code_reviewer import CodeReviewer, MrChatReviewer
from biz.utils.im import notifier
from biz.utils.log import logger

_INCREMENTAL_REVIEW_PREFIX = "[增量审查：本次仅包含自上次审核以来的新增提交] "
_LOW_SCORE_BLOCK_DEFAULT_THRESHOLD = 60
_REVIEW_SCORE_HIGH_THRESHOLD = 90   # score > this → tada emoji
_REVIEW_SCORE_LOW_THRESHOLD = 60    # score < this → cold_sweat emoji

def _try_block_mr_if_low_score(handler: MergeRequestHandler, review_result: str):
    """低分阻止合并：若 AI 审核总分低于阈值，在 MR 中创建未解决讨论。

    仅在 LOW_SCORE_BLOCK_MR_ENABLED=1 时生效，且不影响正常审核流程。
    """
    if os.environ.get('LOW_SCORE_BLOCK_MR_ENABLED', '0') != '1':
        return

    score = CodeReviewer.extract_review_score(review_result)
    if score is None:
        logger.info("Low-score block: no valid score found in review result, skipping.")
        return

    try:
        threshold = int(os.environ.get('LOW_SCORE_BLOCK_MR_THRESHOLD', str(_LOW_SCORE_BLOCK_DEFAULT_THRESHOLD)))
    except ValueError:
        threshold = _LOW_SCORE_BLOCK_DEFAULT_THRESHOLD

    if score >= threshold:
        logger.info(f"Low-score block: score={score} >= threshold={threshold}, no block needed.")
        return

    logger.info(f"Low-score block: score={score} < threshold={threshold}, creating blocking discussion.")
    try:
        handler.create_low_score_block_discussion(score, threshold)
    except Exception as e:
        logger.error(f"Low-score block: failed to create blocking discussion: {e}")


def handle_push_event(webhook_data: dict, gitlab_token: str, gitlab_url: str, gitlab_url_slug: str):
    push_review_enabled = os.environ.get('PUSH_REVIEW_ENABLED', '0') == '1'
    try:
        handler = PushHandler(webhook_data, gitlab_token, gitlab_url)
        logger.info('Push Hook event received')
        commits = handler.get_push_commits()
        if not commits:
            logger.error('Failed to get commits')
            return

        review_result = None
        score = 0
        additions = 0
        deletions = 0
        if push_review_enabled:
            # 获取PUSH的changes
            changes = handler.get_push_changes()
            logger.info('changes: %s', changes)
            changes = filter_changes(changes)
            if not changes:
                logger.info('未检测到PUSH代码的修改,修改文件可能不满足SUPPORTED_EXTENSIONS。')
            review_result = "关注的文件没有修改"

            if len(changes) > 0:
                commits_text = ';'.join(commit.get('message', '').strip() for commit in commits)
                review_result = CodeReviewer().review_and_strip_code(str(changes), commits_text)
                score = CodeReviewer.parse_review_score(review_text=review_result)
                for item in changes:
                    additions += item['additions']
                    deletions += item['deletions']
            # 将review结果提交到Gitlab的 notes
            handler.add_push_notes(f'Auto Review Result: \n{review_result}')

        event_manager['push_reviewed'].send(PushReviewEntity(
            project_name=webhook_data['project']['name'],
            author=webhook_data['user_username'],
            author_name=webhook_data.get('user_name', ''),
            branch=webhook_data.get('ref', '').replace('refs/heads/', ''),
            updated_at=int(datetime.now().timestamp()),  # 当前时间
            commits=commits,
            score=score,
            review_result=review_result,
            url_slug=gitlab_url_slug,
            webhook_data=webhook_data,
            additions=additions,
            deletions=deletions,
        ))

    except Exception as e:
        error_message = f'服务出现未知错误: {str(e)}\n{traceback.format_exc()}'
        notifier.send_notification(content=error_message)
        logger.error('出现未知错误: %s', error_message)


def handle_merge_request_event(webhook_data: dict, gitlab_token: str, gitlab_url: str, gitlab_url_slug: str):
    '''
    处理Merge Request Hook事件
    :param webhook_data:
    :param gitlab_token:
    :param gitlab_url:
    :param gitlab_url_slug:
    :return:
    '''
    merge_review_only_protected_branches = os.environ.get('MERGE_REVIEW_ONLY_PROTECTED_BRANCHES_ENABLED', '0') == '1'
    draft_mr_review_enabled = os.environ.get('DRAFT_MR_REVIEW_ENABLED', '0') == '1'
    try:
        # 解析Webhook数据
        handler = MergeRequestHandler(webhook_data, gitlab_token, gitlab_url)
        logger.info('Merge Request Hook event received')

        # 判断是否为draft（草稿）MR，默认跳过草稿MR的审查
        object_attributes = webhook_data.get('object_attributes', {})
        is_draft = object_attributes.get('draft') or object_attributes.get('work_in_progress')
        if is_draft and not draft_mr_review_enabled:
            msg = f"[通知] MR为草稿（draft），未触发AI审查。\n项目: {webhook_data['project']['name']}\n作者: {webhook_data['user']['username']}\n源分支: {object_attributes.get('source_branch')}\n目标分支: {object_attributes.get('target_branch')}\n链接: {object_attributes.get('url')}"
            notifier.send_notification(content=msg)
            logger.info("MR为draft，仅发送通知，不触发AI review。如需对draft MR触发审查，请设置DRAFT_MR_REVIEW_ENABLED=1。")
            return

        # 如果开启了仅review projected branches的，判断当前目标分支是否为projected branches
        if merge_review_only_protected_branches and not handler.target_branch_protected():
            logger.info("Merge Request target branch not match protected branches, ignored.")
            return

        if handler.action not in ['open', 'update']:
            logger.info(f"Merge Request Hook event, action={handler.action}, ignored.")
            return

        # 提取 MR 唯一标识信息及当前最新 commit id
        last_commit_id = object_attributes.get('last_commit', {}).get('id', '')
        project_name = webhook_data['project']['name']
        source_branch = object_attributes.get('source_branch', '')
        target_branch = object_attributes.get('target_branch', '')
        mr_iid = object_attributes.get('iid')  # GitLab per-project MR IID (e.g. 42 for !42)

        # 如果当前 commit 已经审核过，则跳过（幂等去重）
        if last_commit_id:
            if ReviewService.check_mr_last_commit_id_exists(project_name, source_branch, target_branch,
                                                            last_commit_id, mr_iid=mr_iid):
                logger.info(f"Merge Request with last_commit_id {last_commit_id} already exists, skipping review for {project_name}.")
                return

        # 获取 Merge Request 的 commits（提前获取以支持增量审查的合并提交过滤）
        commits = handler.get_merge_request_commits()
        if not commits:
            logger.error('Failed to get commits')
            return

        # 过滤合并提交（merge commits），合并提交的 parent_ids 长度大于 1
        # 合并提交是将其他分支合入当前分支产生的提交，其代码变更通常已在相应分支的审查中处理过
        non_merge_commits = [c for c in commits if len(c.get('parent_ids', [])) <= 1]

        # 尝试增量审核：获取上次审核的 commit id，若存在则只审核新增部分
        is_incremental = False
        changes = None
        prev_commit_id = ReviewService.get_last_mr_review_commit_id(project_name, source_branch, target_branch,
                                                                     mr_iid=mr_iid)
        if prev_commit_id and last_commit_id and prev_commit_id != last_commit_id:
            # 检测自上次审核以来的新提交是否全部为合并提交（合入其他分支的操作）
            # 若是，则这些变更已在源分支的推送审查或其他 MR 审查中处理过，跳过本次审查以避免重复审核
            commit_ids = [c.get('id') for c in commits]
            if prev_commit_id in commit_ids:
                prev_idx = commit_ids.index(prev_commit_id)
                # GitLab 按最新在前排序；commits[:prev_idx] 取比 prev_commit_id 更新的提交。
                # 因 prev_commit_id != last_commit_id（上方已过滤），prev_idx 不会为 0。
                new_commits_since_last_review = commits[:prev_idx]
                if new_commits_since_last_review and all(
                    len(c.get('parent_ids', [])) > 1 for c in new_commits_since_last_review
                ):
                    logger.info(
                        "All new commits since last review are merge commits (merging other branches), "
                        "skipping review to avoid duplicate review of already-reviewed code."
                    )
                    return
            try:
                incremental_diffs = handler.repository_compare(prev_commit_id, last_commit_id)
                if incremental_diffs:
                    filtered = filter_changes(incremental_diffs)
                    if filtered:
                        changes = filtered
                        is_incremental = True
                        logger.info(f"Incremental MR review: {prev_commit_id} -> {last_commit_id}, {len(changes)} file(s).")
                    else:
                        logger.info("Incremental diff has no relevant file changes, skipping review.")
                        return  # 有新 commit 但均不涉及受支持文件类型，不触发全量审核
                else:
                    logger.info("Incremental diff is empty (possible force push / rebase), falling back to full review.")
            except Exception as e:
                logger.warning(f"Incremental diff failed: {e}, falling back to full review.")

        if not is_incremental:
            # 全量审核
            raw_changes = handler.get_merge_request_changes()
            logger.info('changes: %s', raw_changes)
            changes = filter_changes(raw_changes)
            if not changes:
                logger.info('未检测到有关代码的修改,修改文件可能不满足SUPPORTED_EXTENSIONS。')
                return

        # 统计本次新增、删除的代码总数
        additions = sum(item.get('additions', 0) for item in changes)
        deletions = sum(item.get('deletions', 0) for item in changes)

        # 创建占位评论，并在其上添加 eyes 表情，表示审核正在进行中
        placeholder_note_id = None
        placeholder_eyes_added = False
        try:
            placeholder_note_id = handler.create_mr_note("👀 AI正在审核中...")
        except Exception as _e:
            logger.warning(f"Failed to create placeholder note: {_e}")
        if placeholder_note_id:
            try:
                award_id = handler.award_emoji_to_note(placeholder_note_id, "eyes")
                placeholder_eyes_added = award_id is not None
            except Exception as _e:
                logger.warning(f"Failed to award eyes emoji to placeholder note: {_e}")

        # review 代码 - 使用非合并提交的消息作为上下文，过滤掉"Merge branch X into Y"等无实际意义的提交
        # 若所有提交均为合并提交（极罕见场景），则退回使用全量 commits，保证上下文不为空
        commits_for_context = non_merge_commits if non_merge_commits else commits
        commits_text = ';'.join(commit.get('message', '').strip() for commit in commits_for_context)
        if is_incremental:
            commits_text = _INCREMENTAL_REVIEW_PREFIX + commits_text
        try:
            review_result = CodeReviewer().review_and_strip_code(str(changes), commits_text)
        except Exception:
            # 清理占位评论及 eyes 表情，避免"审核中"状态永久停留
            if placeholder_note_id:
                try:
                    handler.update_mr_note(placeholder_note_id, "⚠️ AI审核未能完成，请稍后重试。")
                except Exception as _e:
                    logger.warning(f"Failed to update placeholder note during error cleanup: {_e}")
                if placeholder_eyes_added:
                    try:
                        handler.remove_note_award_emoji(placeholder_note_id, "eyes")
                    except Exception as _e:
                        logger.warning(f"Failed to remove eyes emoji during error cleanup: {_e}")
            raise

        # 将review结果写入占位评论（编辑），失败则回退为新建评论
        note_for_emoji = placeholder_note_id
        if placeholder_note_id:
            try:
                updated = handler.update_mr_note(placeholder_note_id, f'Auto Review Result: \n{review_result}')
                if not updated:
                    raise RuntimeError("update_mr_note returned False")
            except Exception as _e:
                logger.warning(f"Failed to update placeholder note, falling back to new note: {_e}")
                handler.add_merge_request_notes(f'Auto Review Result: \n{review_result}')
                note_for_emoji = None
        else:
            handler.add_merge_request_notes(f'Auto Review Result: \n{review_result}')

        # 移除 eyes 表情
        if placeholder_note_id and placeholder_eyes_added:
            try:
                handler.remove_note_award_emoji(placeholder_note_id, "eyes")
            except Exception as _e:
                logger.warning(f"Failed to remove eyes emoji: {_e}")

        # 根据评分添加结果表情（仅当占位评论成功编辑时）
        if note_for_emoji:
            try:
                score = CodeReviewer.extract_review_score(review_result)
                if score is not None:
                    if score > _REVIEW_SCORE_HIGH_THRESHOLD:
                        handler.award_emoji_to_note(note_for_emoji, "tada")
                    elif score < _REVIEW_SCORE_LOW_THRESHOLD:
                        handler.award_emoji_to_note(note_for_emoji, "cold_sweat")
            except Exception as _e:
                logger.warning(f"Failed to apply score emoji: {_e}")

        # dispatch merge_request_reviewed event
        event_manager['merge_request_reviewed'].send(
            MergeRequestReviewEntity(
                project_name=webhook_data['project']['name'],
                author=webhook_data['user']['username'],
                author_name=webhook_data['user'].get('name', ''),
                source_branch=webhook_data['object_attributes']['source_branch'],
                target_branch=webhook_data['object_attributes']['target_branch'],
                updated_at=int(datetime.now().timestamp()),
                commits=commits,
                score=CodeReviewer.parse_review_score(review_text=review_result),
                url=webhook_data['object_attributes']['url'],
                review_result=review_result,
                url_slug=gitlab_url_slug,
                webhook_data=webhook_data,
                additions=additions,
                deletions=deletions,
                last_commit_id=last_commit_id,
                mr_iid=mr_iid,
            )
        )

        # 低分阻止合并：在标准 MR 审核完成后执行，不影响正常审核流程
        _try_block_mr_if_low_score(handler, review_result)

    except Exception as e:
        error_message = f'AI Code Review 服务出现未知错误: {str(e)}\n{traceback.format_exc()}'
        notifier.send_notification(content=error_message)
        logger.error('出现未知错误: %s', error_message)


def handle_note_event(webhook_data: dict, gitlab_token: str, gitlab_url: str, gitlab_url_slug: str):
    """
    处理 GitLab note（评论）Webhook 事件，支持 @机器人 交互：
    - 仅 @机器人 => 触发 MR 代码审查
    - @机器人 + 额外文本 => 对话式 AI 回复
    """
    bot_username = os.environ.get('GITLAB_BOT_USERNAME', '').strip()
    if not bot_username:
        logger.info("GITLAB_BOT_USERNAME not configured, note event ignored.")
        return

    try:
        handler = NoteHandler(webhook_data, gitlab_token, gitlab_url)
        logger.info('Note Hook event received')

        # 目前仅处理 MR 上的评论
        if handler.noteable_type != 'MergeRequest':
            logger.info(f"Note event noteable_type={handler.noteable_type}, only MergeRequest notes are handled.")
            return

        if not handler.merge_request_iid:
            logger.info("Note event: merge_request_iid not found, ignored.")
            return

        # 仅处理新建评论（action == 'create'），忽略编辑等其他操作，避免重复触发 AI 工作流
        if handler.action and handler.action != 'create':
            logger.info(f"Note event action={handler.action!r}, only 'create' actions are handled.")
            return

        note_body = handler.note or ''
        is_mentioned, extra_text = parse_bot_mention(note_body, bot_username)

        if not is_mentioned:
            logger.info("Note event: bot not mentioned, ignored.")
            return

        if not extra_text:
            # 仅 @机器人，触发代码审查
            logger.info("Note event: bot mentioned without extra text, triggering code review.")

            project_name = webhook_data.get('project', {}).get('name', '')
            source_branch = handler.source_branch
            target_branch = handler.target_branch
            current_commit_id = handler.last_commit_id
            mr_iid = handler.merge_request_iid  # GitLab per-project MR IID
            mr_ident_available = bool(current_commit_id and project_name and source_branch and target_branch)

            # 如果当前 commit 已经审核过，提示无新增改动
            if mr_ident_available:
                if ReviewService.check_mr_last_commit_id_exists(project_name, source_branch, target_branch,
                                                                current_commit_id, mr_iid=mr_iid):
                    logger.info("Note event: no new changes since last review.")
                    handler.add_merge_request_note('当前 MR 自上次审核后无新增代码变更，无需重复审核。')
                    return

            # 尝试增量审核
            is_incremental = False
            changes = None
            if mr_ident_available:
                prev_commit_id = ReviewService.get_last_mr_review_commit_id(project_name, source_branch,
                                                                             target_branch, mr_iid=mr_iid)
                if prev_commit_id and prev_commit_id != current_commit_id:
                    try:
                        incremental_diffs = handler.repository_compare(prev_commit_id, current_commit_id)
                        if incremental_diffs:
                            filtered = filter_changes(incremental_diffs)
                            if filtered:
                                changes = filtered
                                is_incremental = True
                                logger.info(f"Note event incremental review: {prev_commit_id} -> {current_commit_id}, {len(changes)} file(s).")
                            else:
                                logger.info("Note event incremental diff has no relevant changes, skipping review.")
                                handler.add_merge_request_note('当前 MR 自上次审核后新增提交未涉及受支持的文件类型，无需审核。')
                                return
                        else:
                            logger.info("Note event incremental diff is empty, falling back to full review.")
                    except Exception as e:
                        logger.warning(f"Note event incremental diff failed: {e}, falling back to full review.")

            if not is_incremental:
                changes = filter_changes(handler.get_merge_request_changes())

            if not changes:
                logger.info('Note event review: 未检测到有关代码的修改。')
                handler.add_merge_request_note('关注的文件没有修改，无需 Review。')
                return

            commits = handler.get_merge_request_commits()
            commits_text = ';'.join(commit.get('message', '').strip() for commit in commits)
            if is_incremental:
                commits_text = _INCREMENTAL_REVIEW_PREFIX + commits_text

            # 为触发 note 添加 eyes 表情，表示审核正在进行中
            trigger_note_id = handler.note_id
            trigger_eyes_added = False
            if trigger_note_id:
                try:
                    award_id = handler.award_emoji_to_note(trigger_note_id, "eyes")
                    trigger_eyes_added = award_id is not None
                except Exception as _e:
                    logger.warning(f"Failed to award eyes emoji to trigger note: {_e}")

            try:
                review_result = CodeReviewer().review_and_strip_code(str(changes), commits_text)
                handler.add_merge_request_note(f'Auto Review Result: \n{review_result}')
            except Exception:
                # 清理 eyes 后重新抛出
                if trigger_note_id and trigger_eyes_added:
                    try:
                        handler.remove_note_award_emoji(trigger_note_id, "eyes")
                    except Exception as _e:
                        logger.warning(f"Failed to remove eyes emoji during error cleanup: {_e}")
                raise

            # 移除 eyes 表情
            if trigger_note_id and trigger_eyes_added:
                try:
                    handler.remove_note_award_emoji(trigger_note_id, "eyes")
                except Exception as _e:
                    logger.warning(f"Failed to remove eyes emoji: {_e}")

            # 根据评分添加结果表情
            if trigger_note_id:
                try:
                    score = CodeReviewer.extract_review_score(review_result)
                    if score is not None:
                        if score > _REVIEW_SCORE_HIGH_THRESHOLD:
                            handler.award_emoji_to_note(trigger_note_id, "tada")
                        elif score < _REVIEW_SCORE_LOW_THRESHOLD:
                            handler.award_emoji_to_note(trigger_note_id, "cold_sweat")
                except Exception as _e:
                    logger.warning(f"Failed to apply score emoji to trigger note: {_e}")

            # 保存审核记录，使后续触发可以正确进行增量判断
            if mr_ident_available:
                additions = sum(item.get('additions', 0) for item in changes)
                deletions = sum(item.get('deletions', 0) for item in changes)
                ReviewService.insert_mr_review_log(MergeRequestReviewEntity(
                    project_name=project_name,
                    author=webhook_data.get('user', {}).get('username', ''),
                    author_name=webhook_data.get('user', {}).get('name', ''),
                    source_branch=source_branch,
                    target_branch=target_branch,
                    updated_at=int(datetime.now().timestamp()),
                    commits=commits,
                    score=CodeReviewer.parse_review_score(review_text=review_result),
                    url=webhook_data.get('merge_request', {}).get('url', ''),
                    review_result=review_result,
                    url_slug=gitlab_url_slug,
                    webhook_data=webhook_data,
                    additions=additions,
                    deletions=deletions,
                    last_commit_id=current_commit_id,
                    mr_iid=mr_iid,
                ))
        else:
            # @机器人 + 额外文本，走对话式回复
            logger.info(f"Note event: bot mentioned with extra text, generating chat reply. question={extra_text!r}")

            changes = handler.get_merge_request_changes()
            changes = filter_changes(changes)
            commits = handler.get_merge_request_commits()
            commits_text = ';'.join(commit.get('message', '').strip() for commit in commits)
            diffs_text = str(changes) if changes else ''

            reply = MrChatReviewer().chat(
                user_question=extra_text,
                diffs_text=diffs_text,
                commits_text=commits_text,
            )
            handler.add_merge_request_note(reply)

    except Exception as e:
        error_message = f'AI Code Review note 事件处理出现未知错误: {str(e)}\n{traceback.format_exc()}'
        notifier.send_notification(content=error_message)
        logger.error('出现未知错误: %s', error_message)


def handle_github_push_event(webhook_data: dict, github_token: str, github_url: str, github_url_slug: str):
    push_review_enabled = os.environ.get('PUSH_REVIEW_ENABLED', '0') == '1'
    try:
        handler = GithubPushHandler(webhook_data, github_token, github_url)
        logger.info('GitHub Push event received')
        commits = handler.get_push_commits()
        if not commits:
            logger.error('Failed to get commits')
            return

        review_result = None
        score = 0
        additions = 0
        deletions = 0
        if push_review_enabled:
            # 获取PUSH的changes
            changes = handler.get_push_changes()
            logger.info('changes: %s', changes)
            changes = filter_github_changes(changes)
            if not changes:
                logger.info('未检测到PUSH代码的修改,修改文件可能不满足SUPPORTED_EXTENSIONS。')
            review_result = "关注的文件没有修改"

            if len(changes) > 0:
                commits_text = ';'.join(commit.get('message', '').strip() for commit in commits)
                review_result = CodeReviewer().review_and_strip_code(str(changes), commits_text)
                score = CodeReviewer.parse_review_score(review_text=review_result)
                for item in changes:
                    additions += item.get('additions', 0)
                    deletions += item.get('deletions', 0)
            # 将review结果提交到GitHub的 notes
            handler.add_push_notes(f'Auto Review Result: \n{review_result}')

        event_manager['push_reviewed'].send(PushReviewEntity(
            project_name=webhook_data['repository']['name'],
            author=webhook_data['sender']['login'],
            author_name=webhook_data.get('pusher', {}).get('name', ''),
            branch=webhook_data['ref'].replace('refs/heads/', ''),
            updated_at=int(datetime.now().timestamp()),  # 当前时间
            commits=commits,
            score=score,
            review_result=review_result,
            url_slug=github_url_slug,
            webhook_data=webhook_data,
            additions=additions,
            deletions=deletions,
        ))

    except Exception as e:
        error_message = f'服务出现未知错误: {str(e)}\n{traceback.format_exc()}'
        notifier.send_notification(content=error_message)
        logger.error('出现未知错误: %s', error_message)


def handle_github_pull_request_event(webhook_data: dict, github_token: str, github_url: str, github_url_slug: str):
    '''
    处理GitHub Pull Request 事件
    :param webhook_data:
    :param github_token:
    :param github_url:
    :param github_url_slug:
    :return:
    '''
    merge_review_only_protected_branches = os.environ.get('MERGE_REVIEW_ONLY_PROTECTED_BRANCHES_ENABLED', '0') == '1'
    try:
        # 解析Webhook数据
        handler = GithubPullRequestHandler(webhook_data, github_token, github_url)
        logger.info('GitHub Pull Request event received')
        # 如果开启了仅review projected branches的，判断当前目标分支是否为projected branches
        if merge_review_only_protected_branches and not handler.target_branch_protected():
            logger.info("Merge Request target branch not match protected branches, ignored.")
            return

        if handler.action not in ['opened', 'synchronize']:
            logger.info(f"Pull Request Hook event, action={handler.action}, ignored.")
            return

        # 检查GitHub Pull Request的last_commit_id是否已经存在，如果存在则跳过处理
        github_last_commit_id = webhook_data['pull_request']['head']['sha']
        if github_last_commit_id:
            project_name = webhook_data['repository']['name']
            source_branch = webhook_data['pull_request']['head']['ref']
            target_branch = webhook_data['pull_request']['base']['ref']
            
            if ReviewService.check_mr_last_commit_id_exists(project_name, source_branch, target_branch, github_last_commit_id):
                logger.info(f"Pull Request with last_commit_id {github_last_commit_id} already exists, skipping review for {project_name}.")
                return

        # 仅仅在PR创建或更新时进行Code Review
        # 获取Pull Request的changes
        changes = handler.get_pull_request_changes()
        logger.info('changes: %s', changes)
        changes = filter_github_changes(changes)
        if not changes:
            logger.info('未检测到有关代码的修改,修改文件可能不满足SUPPORTED_EXTENSIONS。')
            return
        # 统计本次新增、删除的代码总数
        additions = 0
        deletions = 0
        for item in changes:
            additions += item.get('additions', 0)
            deletions += item.get('deletions', 0)

        # 获取Pull Request的commits
        commits = handler.get_pull_request_commits()
        if not commits:
            logger.error('Failed to get commits')
            return

        # review 代码
        commits_text = ';'.join(commit.get('message', '').strip() for commit in commits)
        review_result = CodeReviewer().review_and_strip_code(str(changes), commits_text)

        # 将review结果提交到GitHub的 notes
        handler.add_pull_request_notes(f'Auto Review Result: \n{review_result}')

        # dispatch pull_request_reviewed event
        event_manager['merge_request_reviewed'].send(
            MergeRequestReviewEntity(
                project_name=webhook_data['repository']['name'],
                author=webhook_data['pull_request']['user']['login'],
                author_name=webhook_data['pull_request']['user'].get('name', ''),
                source_branch=webhook_data['pull_request']['head']['ref'],
                target_branch=webhook_data['pull_request']['base']['ref'],
                updated_at=int(datetime.now().timestamp()),
                commits=commits,
                score=CodeReviewer.parse_review_score(review_text=review_result),
                url=webhook_data['pull_request']['html_url'],
                review_result=review_result,
                url_slug=github_url_slug,
                webhook_data=webhook_data,
                additions=additions,
                deletions=deletions,
                last_commit_id=github_last_commit_id,
            ))

    except Exception as e:
        error_message = f'服务出现未知错误: {str(e)}\n{traceback.format_exc()}'
        notifier.send_notification(content=error_message)
        logger.error('出现未知错误: %s', error_message)


def handle_gitea_push_event(webhook_data: dict, gitea_token: str, gitea_url: str, gitea_url_slug: str):
    push_review_enabled = os.environ.get('PUSH_REVIEW_ENABLED', '0') == '1'
    try:
        handler = GiteaPushHandler(webhook_data, gitea_token, gitea_url)
        logger.info('Gitea Push event received')
        commits = handler.get_push_commits()
        if not commits:
            logger.error('Failed to get commits')
            return

        review_result = None
        score = 0
        additions = 0
        deletions = 0
        if push_review_enabled:
            changes = handler.get_push_changes()
            logger.info('changes: %s', changes)
            changes = filter_gitea_changes(changes)
            if not changes:
                logger.info('未检测到PUSH代码的修改,修改文件可能不满足SUPPORTED_EXTENSIONS。')
            review_result = "关注的文件没有修改"

            if len(changes) > 0:
                commits_text = ';'.join(commit.get('message', '').strip() for commit in commits)
                review_result = CodeReviewer().review_and_strip_code(str(changes), commits_text)
                score = CodeReviewer.parse_review_score(review_text=review_result)
                for item in changes:
                    additions += item.get('additions', 0)
                    deletions += item.get('deletions', 0)
            handler.add_push_notes(f'Auto Review Result: \n{review_result}')

        repository = webhook_data.get('repository', {})
        sender = webhook_data.get('sender', {}) or webhook_data.get('pusher', {}) or {}

        event_manager['push_reviewed'].send(PushReviewEntity(
            project_name=repository.get('name'),
            author=sender.get('login') or sender.get('username'),
            author_name=sender.get('full_name') or sender.get('login') or sender.get('username') or '',
            branch=handler.branch_name,
            updated_at=int(datetime.now().timestamp()),
            commits=commits,
            score=score,
            review_result=review_result,
            url_slug=gitea_url_slug,
            webhook_data=webhook_data,
            additions=additions,
            deletions=deletions,
        ))

    except Exception as e:
        error_message = f'服务出现未知错误: {str(e)}\n{traceback.format_exc()}'
        notifier.send_notification(content=error_message)
        logger.error('出现未知错误: %s', error_message)


def handle_gitea_pull_request_event(webhook_data: dict, gitea_token: str, gitea_url: str, gitea_url_slug: str):
    merge_review_only_protected_branches = os.environ.get('MERGE_REVIEW_ONLY_PROTECTED_BRANCHES_ENABLED', '0') == '1'
    try:
        handler = GiteaPullRequestHandler(webhook_data, gitea_token, gitea_url)
        logger.info('Gitea Pull Request event received')

        pull_request = webhook_data.get('pull_request', {})

        if merge_review_only_protected_branches and not handler.target_branch_protected():
            logger.info("Pull Request target branch not match protected branches, ignored.")
            return

        if handler.action not in ['opened', 'open', 'reopened', 'synchronize', 'synchronized']:
            logger.info(f"Pull Request Hook event, action={handler.action}, ignored.")
            return

        head_info = pull_request.get('head') or {}
        base_info = pull_request.get('base') or {}

        last_commit_id = head_info.get('sha') or pull_request.get('merge_commit_sha') or pull_request.get('last_commit_id')
        if last_commit_id:
            project_name = webhook_data.get('repository', {}).get('name')
            source_branch = head_info.get('ref') or pull_request.get('head_branch', '')
            target_branch = base_info.get('ref') or pull_request.get('base_branch', '')

            if ReviewService.check_mr_last_commit_id_exists(project_name, source_branch, target_branch, last_commit_id):
                logger.info(f"Pull Request with last_commit_id {last_commit_id} already exists, skipping review for {project_name}.")
                return

        changes = handler.get_pull_request_changes()
        logger.info('changes: %s', changes)
        changes = filter_gitea_changes(changes)
        if not changes:
            logger.info('未检测到有关代码的修改,修改文件可能不满足SUPPORTED_EXTENSIONS。')
            return

        additions = 0
        deletions = 0
        for item in changes:
            additions += item.get('additions', 0)
            deletions += item.get('deletions', 0)

        commits = handler.get_pull_request_commits()
        if not commits:
            logger.error('Failed to get commits for Gitea pull request')
            return

        commits_text = ';'.join(commit.get('message', '').strip() for commit in commits)
        review_result = CodeReviewer().review_and_strip_code(str(changes), commits_text)

        handler.add_pull_request_notes(f'Auto Review Result: \n{review_result}')

        repository = webhook_data.get('repository', {})
        author_info = pull_request.get('user', {}) or webhook_data.get('sender', {}) or {}

        event_manager['merge_request_reviewed'].send(
            MergeRequestReviewEntity(
                project_name=repository.get('name'),
                author=author_info.get('login') or author_info.get('username'),
                author_name=author_info.get('full_name') or author_info.get('login') or author_info.get('username') or '',
                source_branch=head_info.get('ref') or pull_request.get('head_branch', ''),
                target_branch=base_info.get('ref') or pull_request.get('base_branch', ''),
                updated_at=int(datetime.now().timestamp()),
                commits=commits,
                score=CodeReviewer.parse_review_score(review_text=review_result),
                url=pull_request.get('html_url') or pull_request.get('url'),
                review_result=review_result,
                url_slug=gitea_url_slug,
                webhook_data=webhook_data,
                additions=additions,
                deletions=deletions,
                last_commit_id=last_commit_id,
            ))

    except Exception as e:
        error_message = f'AI Code Review 服务出现未知错误: {str(e)}\n{traceback.format_exc()}'
        notifier.send_notification(content=error_message)
        logger.error('出现未知错误: %s', error_message)
