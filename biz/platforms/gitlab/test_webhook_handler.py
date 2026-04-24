#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# @Time    : 2025/3/18 17:58
# @Author  : Arrow
from unittest import TestCase, main

from biz.platforms.gitlab.webhook_handler import PushHandler, NoteHandler, parse_bot_mention


# @Describe:
class TestPushHandler(TestCase):
    def setUp(self):
        """设置测试环境"""
        self.sample_webhook_data = {
            'event_name': 'push',
            'project': {
                'id': 0
            },
        }
        self.gitlab_token = ''
        self.gitlab_url = ''

        # 创建PushHandler实例
        self.handler = PushHandler(self.sample_webhook_data, self.gitlab_token, self.gitlab_url)

    def test_get_parent_commit_id(self):
        """测试获取父提交ID"""
        commit_id = ''
        # 调用测试方法
        parent_id = self.handler.get_parent_commit_id(commit_id)

        self.assertTrue(parent_id)


class TestParseBotMention(TestCase):
    """测试 parse_bot_mention 工具函数"""

    def test_not_mentioned(self):
        """评论中没有 @ 机器人"""
        is_mentioned, extra = parse_bot_mention("This is a normal comment", "bot")
        self.assertFalse(is_mentioned)
        self.assertEqual(extra, "")

    def test_only_mention(self):
        """仅 @机器人，无额外文本"""
        is_mentioned, extra = parse_bot_mention("@bot", "bot")
        self.assertTrue(is_mentioned)
        self.assertEqual(extra, "")

    def test_mention_with_whitespace_only(self):
        """@机器人 后只有空白字符"""
        is_mentioned, extra = parse_bot_mention("  @bot   ", "bot")
        self.assertTrue(is_mentioned)
        self.assertEqual(extra, "")

    def test_mention_with_extra_text(self):
        """@机器人 后附有额外文本"""
        is_mentioned, extra = parse_bot_mention("@bot 请帮我看看这段代码有什么问题", "bot")
        self.assertTrue(is_mentioned)
        self.assertEqual(extra, "请帮我看看这段代码有什么问题")

    def test_case_insensitive(self):
        """@mention 大小写不敏感"""
        is_mentioned, extra = parse_bot_mention("@BOT hello", "bot")
        self.assertTrue(is_mentioned)
        self.assertEqual(extra, "hello")

    def test_empty_note(self):
        """空评论"""
        is_mentioned, extra = parse_bot_mention("", "bot")
        self.assertFalse(is_mentioned)
        self.assertEqual(extra, "")

    def test_empty_bot_username(self):
        """机器人用户名未配置"""
        is_mentioned, extra = parse_bot_mention("@bot hello", "")
        self.assertFalse(is_mentioned)
        self.assertEqual(extra, "")

    def test_mention_among_multiple_users(self):
        """评论中 @ 了多个用户，机器人在其中"""
        is_mentioned, extra = parse_bot_mention("@alice @bot @charlie 请 review", "bot")
        self.assertTrue(is_mentioned)
        # 去掉 @bot 之后剩余文本中应包含 "请 review"
        self.assertIn("请 review", extra)

    def test_partial_username_match_not_detected(self):
        """@botuser 不应匹配 bot"""
        is_mentioned, extra = parse_bot_mention("@botuser hello", "bot")
        self.assertFalse(is_mentioned)
        self.assertEqual(extra, "")


class TestNoteHandler(TestCase):
    """测试 NoteHandler 基本解析逻辑"""

    def _make_webhook_data(self, noteable_type="MergeRequest", note="@bot review", mr_iid=1):
        return {
            'object_kind': 'note',
            'project_id': 42,
            'project': {'id': 42},
            'object_attributes': {
                'note': note,
                'noteable_type': noteable_type,
                'discussion_id': 'abc123',
            },
            'merge_request': {
                'id': 100,
                'iid': mr_iid,
            },
        }

    def test_parse_mr_note(self):
        """测试 MR note 的字段解析"""
        data = self._make_webhook_data()
        handler = NoteHandler(data, '', '')
        self.assertEqual(handler.project_id, 42)
        self.assertEqual(handler.noteable_type, 'MergeRequest')
        self.assertEqual(handler.merge_request_iid, 1)
        self.assertEqual(handler.note, '@bot review')

    def test_parse_non_mr_note(self):
        """非 MR 类型的 note 不应设置 merge_request_iid"""
        data = self._make_webhook_data(noteable_type='Commit')
        handler = NoteHandler(data, '', '')
        self.assertEqual(handler.noteable_type, 'Commit')
        self.assertIsNone(handler.merge_request_iid)

    def test_missing_fields(self):
        """payload 缺少某些字段时不应抛异常"""
        handler = NoteHandler({}, '', '')
        self.assertIsNone(handler.project_id)
        self.assertEqual(handler.note, '')
        self.assertIsNone(handler.merge_request_iid)


if __name__ == '__main__':
    main()
