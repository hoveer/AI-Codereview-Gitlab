#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# @Time    : 2025/3/18 17:58
# @Author  : Arrow
from unittest import TestCase, main
from unittest.mock import patch, MagicMock

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

    def test_parse_note_action_create(self):
        """note payload 含 action='create' 时应正确解析"""
        data = self._make_webhook_data()
        data['object_attributes']['action'] = 'create'
        handler = NoteHandler(data, '', '')
        self.assertEqual(handler.action, 'create')

    def test_parse_note_action_update(self):
        """note payload 含 action='update' 时应正确解析"""
        data = self._make_webhook_data()
        data['object_attributes']['action'] = 'update'
        handler = NoteHandler(data, '', '')
        self.assertEqual(handler.action, 'update')

    def test_parse_note_action_missing(self):
        """note payload 不含 action 时 action 应为空字符串"""
        data = self._make_webhook_data()
        handler = NoteHandler(data, '', '')
        self.assertEqual(handler.action, '')

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

    @patch('biz.platforms.gitlab.webhook_handler.requests.post')
    def test_add_note_replies_in_discussion_thread(self, mock_post):
        """当 discussion_id 存在时，应使用讨论回复接口"""
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.text = ''
        mock_post.return_value = mock_resp

        data = self._make_webhook_data()  # discussion_id='abc123'
        handler = NoteHandler(data, 'token', 'https://gitlab.example.com')
        handler.add_merge_request_note('test reply')

        called_url = mock_post.call_args[0][0]
        self.assertIn('/discussions/abc123/notes', called_url)
        self.assertNotIn('/merge_requests/1/notes', called_url.replace('/discussions/', ''))

    @patch('biz.platforms.gitlab.webhook_handler.requests.post')
    def test_add_note_falls_back_to_top_level_when_no_discussion(self, mock_post):
        """当 discussion_id 为空时，应退回到顶层 MR notes 接口"""
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.text = ''
        mock_post.return_value = mock_resp

        data = self._make_webhook_data()
        data['object_attributes']['discussion_id'] = None
        handler = NoteHandler(data, 'token', 'https://gitlab.example.com')
        handler.add_merge_request_note('top-level note')

        called_url = mock_post.call_args[0][0]
        self.assertTrue(called_url.endswith('/merge_requests/1/notes'))

    def test_parse_author_fields(self):
        """webhook payload 中的 user 字段应被正确解析为作者信息"""
        data = self._make_webhook_data()
        data['user'] = {'id': 7, 'username': 'alice', 'name': 'Alice A'}
        handler = NoteHandler(data, '', '')
        self.assertEqual(handler.author_username, 'alice')
        self.assertEqual(handler.author_id, 7)
        self.assertEqual(handler.author_name, 'Alice A')

    def test_parse_author_fields_missing(self):
        """payload 不含 user 字段时，作者字段应为默认空值"""
        data = self._make_webhook_data()
        handler = NoteHandler(data, '', '')
        self.assertEqual(handler.author_username, '')
        self.assertIsNone(handler.author_id)
        self.assertEqual(handler.author_name, '')

    @patch('biz.platforms.gitlab.webhook_handler.requests.get')
    def test_get_discussion_notes_success(self, mock_get):
        """get_discussion_notes 成功时应返回 notes 列表"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'notes': [{'id': 1, 'body': 'hello'}]}
        mock_get.return_value = mock_resp

        data = self._make_webhook_data()
        handler = NoteHandler(data, 'token', 'https://gitlab.example.com')
        notes = handler.get_discussion_notes()

        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]['id'], 1)
        called_url = mock_get.call_args[0][0]
        self.assertIn('/discussions/abc123', called_url)

    @patch('biz.platforms.gitlab.webhook_handler.requests.get')
    def test_get_discussion_notes_api_failure(self, mock_get):
        """get_discussion_notes API 调用失败时应返回空列表"""
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = 'Not found'
        mock_get.return_value = mock_resp

        data = self._make_webhook_data()
        handler = NoteHandler(data, 'token', 'https://gitlab.example.com')
        notes = handler.get_discussion_notes()

        self.assertEqual(notes, [])

    def test_get_discussion_notes_no_discussion_id(self):
        """discussion_id 为空时 get_discussion_notes 应直接返回空列表"""
        data = self._make_webhook_data()
        data['object_attributes']['discussion_id'] = None
        handler = NoteHandler(data, 'token', 'https://gitlab.example.com')
        notes = handler.get_discussion_notes()
        self.assertEqual(notes, [])


class TestHandleNoteEventAutoChat(TestCase):
    """测试 handle_note_event 的 discussion 自动续聊触发逻辑"""

    def _make_webhook(self, note='hello', author='alice', discussion_id='disc1', note_id=99):
        return {
            'object_kind': 'note',
            'project_id': 10,
            'project': {'id': 10, 'name': 'myproject'},
            'user': {'id': 5, 'username': author, 'name': 'Alice'},
            'object_attributes': {
                'id': note_id,
                'note': note,
                'noteable_type': 'MergeRequest',
                'discussion_id': discussion_id,
                'action': 'create',
            },
            'merge_request': {
                'id': 200,
                'iid': 3,
                'source_branch': 'feature',
                'target_branch': 'main',
                'last_commit': {'id': 'abc'},
                'url': 'https://gitlab.example.com/proj/-/merge_requests/3',
            },
        }

    @patch('biz.queue.worker.MrChatReviewer')
    @patch('biz.queue.worker.NoteHandler')
    @patch.dict('os.environ', {'GITLAB_BOT_USERNAME': 'aibot'})
    def test_bot_self_authored_note_ignored(self, MockHandler, MockChatReviewer):
        """机器人自己发出的评论不应触发任何 AI 处理"""
        from biz.queue.worker import handle_note_event
        mock_handler = MagicMock()
        mock_handler.noteable_type = 'MergeRequest'
        mock_handler.merge_request_iid = 3
        mock_handler.action = 'create'
        mock_handler.author_username = 'aibot'
        mock_handler.note = 'AI generated reply'
        MockHandler.return_value = mock_handler

        handle_note_event(self._make_webhook(author='aibot', note='AI generated reply'), '', '', '')

        MockChatReviewer.return_value.chat.assert_not_called()
        mock_handler.add_merge_request_note.assert_not_called()

    @patch('biz.queue.worker.MrChatReviewer')
    @patch('biz.queue.worker.NoteHandler')
    @patch.dict('os.environ', {'GITLAB_BOT_USERNAME': 'aibot'})
    def test_explicit_mention_triggers_chat(self, MockHandler, MockChatReviewer):
        """显式 @机器人 + 文本 仍然正常触发对话模式"""
        from biz.queue.worker import handle_note_event
        mock_handler = MagicMock()
        mock_handler.noteable_type = 'MergeRequest'
        mock_handler.merge_request_iid = 3
        mock_handler.action = 'create'
        mock_handler.author_username = 'alice'
        mock_handler.note = '@aibot what do you think?'
        mock_handler.note_id = 99
        mock_handler.get_merge_request_changes.return_value = []
        mock_handler.get_merge_request_commits.return_value = []
        MockHandler.return_value = mock_handler

        mock_chat = MagicMock()
        mock_chat.chat.return_value = 'AI answer'
        MockChatReviewer.return_value = mock_chat

        handle_note_event(self._make_webhook(note='@aibot what do you think?'), '', '', '')

        mock_chat.chat.assert_called_once()
        mock_handler.add_merge_request_note.assert_called_once_with('AI answer')

    @patch('biz.queue.worker.MrChatReviewer')
    @patch('biz.queue.worker.NoteHandler')
    @patch.dict('os.environ', {'GITLAB_BOT_USERNAME': 'aibot'})
    def test_auto_chat_triggers_when_last_note_is_from_bot(self, MockHandler, MockChatReviewer):
        """无显式 @机器人，但 discussion 中上一条评论来自机器人 => 自动触发对话"""
        from biz.queue.worker import handle_note_event
        mock_handler = MagicMock()
        mock_handler.noteable_type = 'MergeRequest'
        mock_handler.merge_request_iid = 3
        mock_handler.action = 'create'
        mock_handler.author_username = 'alice'
        mock_handler.note = 'Can you elaborate?'
        mock_handler.note_id = 99
        mock_handler.discussion_id = 'disc1'
        mock_handler.get_discussion_notes.return_value = [
            {'id': 10, 'body': 'first comment', 'author': {'username': 'alice'}},
            {'id': 20, 'body': 'AI reply', 'author': {'username': 'aibot'}},
            {'id': 99, 'body': 'Can you elaborate?', 'author': {'username': 'alice'}},
        ]
        mock_handler.get_merge_request_changes.return_value = []
        mock_handler.get_merge_request_commits.return_value = []
        MockHandler.return_value = mock_handler

        mock_chat = MagicMock()
        mock_chat.chat.return_value = 'elaborated AI answer'
        MockChatReviewer.return_value = mock_chat

        handle_note_event(self._make_webhook(note='Can you elaborate?'), '', '', '')

        mock_chat.chat.assert_called_once()
        call_kwargs = mock_chat.chat.call_args[1]
        self.assertEqual(call_kwargs['user_question'], 'Can you elaborate?')
        mock_handler.add_merge_request_note.assert_called_once_with('elaborated AI answer')

    @patch('biz.queue.worker.MrChatReviewer')
    @patch('biz.queue.worker.NoteHandler')
    @patch.dict('os.environ', {'GITLAB_BOT_USERNAME': 'aibot'})
    def test_explicit_mention_triggers_chat(self, MockHandler, MockChatReviewer):
        """显式 @机器人 + 文本 仍然正常触发对话模式，并使用占位评论编辑流程"""
        from biz.queue.worker import handle_note_event
        mock_handler = MagicMock()
        mock_handler.noteable_type = 'MergeRequest'
        mock_handler.merge_request_iid = 3
        mock_handler.action = 'create'
        mock_handler.author_username = 'alice'
        mock_handler.note = '@aibot what do you think?'
        mock_handler.note_id = 99
        mock_handler.create_mr_note.return_value = 200
        mock_handler.update_mr_note.return_value = True
        mock_handler.get_merge_request_changes.return_value = []
        mock_handler.get_merge_request_commits.return_value = []
        MockHandler.return_value = mock_handler

        mock_chat = MagicMock()
        mock_chat.chat.return_value = 'AI answer'
        MockChatReviewer.return_value = mock_chat

        handle_note_event(self._make_webhook(note='@aibot what do you think?'), '', '', '')

        mock_chat.chat.assert_called_once()
        # 应创建占位评论，然后编辑为最终回复，而不是直接 add_merge_request_note
        mock_handler.create_mr_note.assert_called_once_with('👀 AI正在思考中...')
        mock_handler.update_mr_note.assert_called_once()
        update_call_body = mock_handler.update_mr_note.call_args[0][1]
        self.assertIn('AI answer', update_call_body)
        # add_merge_request_note 不应被直接调用作为最终回复
        mock_handler.add_merge_request_note.assert_not_called()

    @patch('biz.queue.worker.MrChatReviewer')
    @patch('biz.queue.worker.NoteHandler')
    @patch.dict('os.environ', {'GITLAB_BOT_USERNAME': 'aibot'})
    def test_auto_chat_triggers_when_last_note_is_from_bot(self, MockHandler, MockChatReviewer):
        """无显式 @机器人，但 discussion 中上一条评论来自机器人 => 自动触发对话，使用占位评论编辑流程"""
        from biz.queue.worker import handle_note_event
        mock_handler = MagicMock()
        mock_handler.noteable_type = 'MergeRequest'
        mock_handler.merge_request_iid = 3
        mock_handler.action = 'create'
        mock_handler.author_username = 'alice'
        mock_handler.note = 'Can you elaborate?'
        mock_handler.note_id = 99
        mock_handler.discussion_id = 'disc1'
        mock_handler.create_mr_note.return_value = 201
        mock_handler.update_mr_note.return_value = True
        mock_handler.get_discussion_notes.return_value = [
            {'id': 10, 'body': 'first comment', 'author': {'username': 'alice'}},
            {'id': 20, 'body': 'AI reply', 'author': {'username': 'aibot'}},
            {'id': 99, 'body': 'Can you elaborate?', 'author': {'username': 'alice'}},
        ]
        mock_handler.get_merge_request_changes.return_value = []
        mock_handler.get_merge_request_commits.return_value = []
        MockHandler.return_value = mock_handler

        mock_chat = MagicMock()
        mock_chat.chat.return_value = 'elaborated AI answer'
        MockChatReviewer.return_value = mock_chat

        handle_note_event(self._make_webhook(note='Can you elaborate?'), '', '', '')

        mock_chat.chat.assert_called_once()
        call_kwargs = mock_chat.chat.call_args[1]
        self.assertEqual(call_kwargs['user_question'], 'Can you elaborate?')
        # 应创建占位评论，然后编辑为最终回复
        mock_handler.create_mr_note.assert_called_once_with('👀 AI正在思考中...')
        mock_handler.update_mr_note.assert_called_once()
        update_call_body = mock_handler.update_mr_note.call_args[0][1]
        self.assertIn('elaborated AI answer', update_call_body)
        mock_handler.add_merge_request_note.assert_not_called()

    @patch('biz.queue.worker.MrChatReviewer')
    @patch('biz.queue.worker.NoteHandler')
    @patch.dict('os.environ', {'GITLAB_BOT_USERNAME': 'aibot'})
    def test_no_auto_chat_when_last_note_not_from_bot(self, MockHandler, MockChatReviewer):
        """无显式 @机器人，discussion 中上一条评论不是机器人 => 不触发"""
        from biz.queue.worker import handle_note_event
        mock_handler = MagicMock()
        mock_handler.noteable_type = 'MergeRequest'
        mock_handler.merge_request_iid = 3
        mock_handler.action = 'create'
        mock_handler.author_username = 'alice'
        mock_handler.note = 'I agree'
        mock_handler.note_id = 99
        mock_handler.discussion_id = 'disc1'
        mock_handler.get_discussion_notes.return_value = [
            {'id': 10, 'body': 'AI comment', 'author': {'username': 'aibot'}},
            {'id': 50, 'body': 'human reply', 'author': {'username': 'bob'}},
            {'id': 99, 'body': 'I agree', 'author': {'username': 'alice'}},
        ]
        MockHandler.return_value = mock_handler

        handle_note_event(self._make_webhook(note='I agree'), '', '', '')

        MockChatReviewer.return_value.chat.assert_not_called()
        mock_handler.create_mr_note.assert_not_called()
        mock_handler.add_merge_request_note.assert_not_called()

    @patch('biz.queue.worker.MrChatReviewer')
    @patch('biz.queue.worker.NoteHandler')
    @patch.dict('os.environ', {'GITLAB_BOT_USERNAME': 'aibot'})
    def test_no_auto_chat_without_discussion_id(self, MockHandler, MockChatReviewer):
        """无显式 @机器人，且 discussion_id 为空 => 不触发"""
        from biz.queue.worker import handle_note_event
        mock_handler = MagicMock()
        mock_handler.noteable_type = 'MergeRequest'
        mock_handler.merge_request_iid = 3
        mock_handler.action = 'create'
        mock_handler.author_username = 'alice'
        mock_handler.note = 'just a comment'
        mock_handler.note_id = 99
        mock_handler.discussion_id = None
        MockHandler.return_value = mock_handler

        handle_note_event(self._make_webhook(note='just a comment', discussion_id=None), '', '', '')

        MockChatReviewer.return_value.chat.assert_not_called()
        mock_handler.create_mr_note.assert_not_called()
        mock_handler.add_merge_request_note.assert_not_called()


class TestNoteHandlerCreateUpdateNote(TestCase):
    """测试 NoteHandler.create_mr_note 和 update_mr_note 方法"""

    def _make_webhook_data(self, discussion_id='disc1'):
        return {
            'object_kind': 'note',
            'project_id': 42,
            'project': {'id': 42},
            'object_attributes': {
                'note': '@bot review',
                'noteable_type': 'MergeRequest',
                'discussion_id': discussion_id,
            },
            'merge_request': {
                'id': 100,
                'iid': 5,
            },
        }

    @patch('biz.platforms.gitlab.webhook_handler.requests.post')
    def test_create_mr_note_in_discussion_thread(self, mock_post):
        """当 discussion_id 存在时，create_mr_note 应向讨论回复接口发请求，并返回 note_id"""
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {'id': 999}
        mock_post.return_value = mock_resp

        handler = NoteHandler(self._make_webhook_data(discussion_id='abc123'),
                              'token', 'https://gitlab.example.com')
        note_id = handler.create_mr_note('👀 AI正在思考中...')

        self.assertEqual(note_id, 999)
        called_url = mock_post.call_args[0][0]
        self.assertIn('/discussions/abc123/notes', called_url)

    @patch('biz.platforms.gitlab.webhook_handler.requests.post')
    def test_create_mr_note_top_level_without_discussion(self, mock_post):
        """当 discussion_id 为空时，create_mr_note 应向顶层 notes 接口发请求，并返回 note_id"""
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {'id': 888}
        mock_post.return_value = mock_resp

        handler = NoteHandler(self._make_webhook_data(discussion_id=None),
                              'token', 'https://gitlab.example.com')
        note_id = handler.create_mr_note('👀 AI正在思考中...')

        self.assertEqual(note_id, 888)
        called_url = mock_post.call_args[0][0]
        self.assertTrue(called_url.endswith('/merge_requests/5/notes'))

    @patch('biz.platforms.gitlab.webhook_handler.requests.post')
    def test_create_mr_note_failure_returns_none(self, mock_post):
        """create_mr_note API 调用失败时应返回 None"""
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = 'Internal Server Error'
        mock_post.return_value = mock_resp

        handler = NoteHandler(self._make_webhook_data(), 'token', 'https://gitlab.example.com')
        note_id = handler.create_mr_note('placeholder')

        self.assertIsNone(note_id)

    @patch('biz.platforms.gitlab.webhook_handler.requests.put')
    def test_update_mr_note_success(self, mock_put):
        """update_mr_note 成功时应返回 True"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_put.return_value = mock_resp

        handler = NoteHandler(self._make_webhook_data(), 'token', 'https://gitlab.example.com')
        result = handler.update_mr_note(999, 'final AI reply')

        self.assertTrue(result)
        called_url = mock_put.call_args[0][0]
        self.assertIn('/notes/999', called_url)
        sent_body = mock_put.call_args[1]['json']['body']
        self.assertEqual(sent_body, 'final AI reply')

    @patch('biz.platforms.gitlab.webhook_handler.requests.put')
    def test_update_mr_note_failure_returns_false(self, mock_put):
        """update_mr_note API 调用失败时应返回 False"""
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = 'Not found'
        mock_put.return_value = mock_resp

        handler = NoteHandler(self._make_webhook_data(), 'token', 'https://gitlab.example.com')
        result = handler.update_mr_note(999, 'body')

        self.assertFalse(result)


class TestHandleNoteEventPlaceholderFlow(TestCase):
    """测试 handle_note_event 中各场景的占位评论编辑流程"""

    def _make_webhook(self, note='@aibot', author='alice', discussion_id='disc1', note_id=99):
        return {
            'object_kind': 'note',
            'project_id': 10,
            'project': {'id': 10, 'name': 'myproject'},
            'user': {'id': 5, 'username': author, 'name': 'Alice'},
            'object_attributes': {
                'id': note_id,
                'note': note,
                'noteable_type': 'MergeRequest',
                'discussion_id': discussion_id,
                'action': 'create',
            },
            'merge_request': {
                'id': 200,
                'iid': 3,
                'source_branch': 'feature',
                'target_branch': 'main',
                'last_commit': {'id': 'abc'},
                'url': 'https://gitlab.example.com/proj/-/merge_requests/3',
            },
        }

    def _make_mock_handler(self, note='@aibot', discussion_id=None):
        mock_handler = MagicMock()
        mock_handler.noteable_type = 'MergeRequest'
        mock_handler.merge_request_iid = 3
        mock_handler.action = 'create'
        mock_handler.author_username = 'alice'
        mock_handler.note = note
        mock_handler.note_id = 99
        mock_handler.discussion_id = discussion_id
        mock_handler.source_branch = 'feature'
        mock_handler.target_branch = 'main'
        mock_handler.last_commit_id = 'abc'
        mock_handler.create_mr_note.return_value = 500
        mock_handler.update_mr_note.return_value = True
        mock_handler.get_merge_request_changes.return_value = []
        mock_handler.get_merge_request_commits.return_value = []
        return mock_handler

    @patch('biz.queue.worker.CodeReviewer')
    @patch('biz.queue.worker.ReviewService')
    @patch('biz.queue.worker.NoteHandler')
    @patch.dict('os.environ', {'GITLAB_BOT_USERNAME': 'aibot'})
    def test_at_ai_review_uses_placeholder_edit(self, MockHandler, MockReview, MockCodeReviewer):
        """@ai 触发标准审查：应先创建占位评论，AI 完成后编辑为正式结果"""
        from biz.queue.worker import handle_note_event
        mock_handler = self._make_mock_handler(note='@aibot')
        mock_handler.get_merge_request_changes.return_value = [
            {'diff': '+line', 'new_path': 'foo.py', 'additions': 1, 'deletions': 0}
        ]
        MockHandler.return_value = mock_handler

        mock_reviewer_instance = MagicMock()
        mock_reviewer_instance.review_and_strip_code.return_value = 'review text'
        MockCodeReviewer.return_value = mock_reviewer_instance
        MockCodeReviewer.extract_review_score.return_value = None
        MockReview.check_mr_last_commit_id_exists.return_value = False
        MockReview.get_last_mr_review_commit_id.return_value = None

        handle_note_event(self._make_webhook(note='@aibot'), '', '', '')

        mock_handler.create_mr_note.assert_called_once_with('👀 AI正在审核中...')
        mock_handler.update_mr_note.assert_called_once()
        update_body = mock_handler.update_mr_note.call_args[0][1]
        self.assertIn('review text', update_body)
        self.assertIn('Auto Review Result', update_body)
        # 直接 add_merge_request_note 不应被用于发布最终审核结果
        mock_handler.add_merge_request_note.assert_not_called()

    @patch('biz.queue.worker.MrChatReviewer')
    @patch('biz.queue.worker.NoteHandler')
    @patch.dict('os.environ', {'GITLAB_BOT_USERNAME': 'aibot'})
    def test_discussion_auto_chat_placeholder_in_thread(self, MockHandler, MockChatReviewer):
        """discussion 自动续聊：占位评论应在讨论线程内创建，AI 完成后编辑"""
        from biz.queue.worker import handle_note_event
        mock_handler = self._make_mock_handler(note='Please explain more', discussion_id='thread42')
        mock_handler.create_mr_note.return_value = 300
        mock_handler.get_discussion_notes.return_value = [
            {'id': 10, 'body': 'bot answer', 'author': {'username': 'aibot'}},
            {'id': 99, 'body': 'Please explain more', 'author': {'username': 'alice'}},
        ]
        MockHandler.return_value = mock_handler

        mock_chat = MagicMock()
        mock_chat.chat.return_value = 'detailed explanation'
        MockChatReviewer.return_value = mock_chat

        handle_note_event(self._make_webhook(note='Please explain more', discussion_id='thread42'), '', '', '')

        mock_handler.create_mr_note.assert_called_once_with('👀 AI正在思考中...')
        mock_handler.update_mr_note.assert_called_once()
        update_body = mock_handler.update_mr_note.call_args[0][1]
        self.assertIn('detailed explanation', update_body)

    @patch('biz.queue.worker.MrChatReviewer')
    @patch('biz.queue.worker.NoteHandler')
    @patch.dict('os.environ', {'GITLAB_BOT_USERNAME': 'aibot'})
    def test_placeholder_fallback_to_add_note_when_create_fails(self, MockHandler, MockChatReviewer):
        """当 create_mr_note 失败（返回 None）时，最终结果应通过 add_merge_request_note 发出"""
        from biz.queue.worker import handle_note_event
        mock_handler = self._make_mock_handler(note='@aibot explain', discussion_id=None)
        mock_handler.create_mr_note.return_value = None  # 创建占位失败
        MockHandler.return_value = mock_handler

        mock_chat = MagicMock()
        mock_chat.chat.return_value = 'fallback reply'
        MockChatReviewer.return_value = mock_chat

        handle_note_event(self._make_webhook(note='@aibot explain'), '', '', '')

        # 创建失败时应通过 add_merge_request_note 发出结果
        mock_handler.add_merge_request_note.assert_called_once()
        call_body = mock_handler.add_merge_request_note.call_args[0][0]
        self.assertIn('fallback reply', call_body)

    @patch('biz.queue.worker.MrChatReviewer')
    @patch('biz.queue.worker.NoteHandler')
    @patch.dict('os.environ', {'GITLAB_BOT_USERNAME': 'aibot'})
    def test_ai_failure_updates_placeholder_to_error(self, MockHandler, MockChatReviewer):
        """AI 调用失败时，占位评论应更新为错误提示，eyes 表情应被移除"""
        from biz.queue.worker import handle_note_event
        mock_handler = self._make_mock_handler(note='@aibot explain', discussion_id=None)
        mock_handler.create_mr_note.return_value = 600
        mock_handler.award_emoji_to_note.return_value = 77
        MockHandler.return_value = mock_handler

        mock_chat = MagicMock()
        mock_chat.chat.side_effect = RuntimeError("LLM timeout")
        MockChatReviewer.return_value = mock_chat

        # 异常被顶层捕获，handle_note_event 不向外抛出
        handle_note_event(self._make_webhook(note='@aibot explain'), '', '', '')

        # 占位评论应更新为错误提示
        mock_handler.update_mr_note.assert_called_once()
        error_body = mock_handler.update_mr_note.call_args[0][1]
        self.assertIn('⚠️', error_body)
        # eyes 表情应被移除
        mock_handler.remove_note_award_emoji.assert_called_once_with(600, 'eyes')


if __name__ == '__main__':
    main()
