from unittest import TestCase
from unittest.mock import MagicMock, patch

from biz.queue.worker import (
    _INCREMENTAL_REVIEW_PREFIX,
    _build_review_commits_text,
    _get_new_commits_since_last_review,
    handle_merge_request_event,
    handle_note_event,
)


class TestWorkerCommitFiltering(TestCase):
    def test_get_new_commits_since_last_review_returns_newer_commits_only(self):
        commits = [
            {'id': 'newest'},
            {'id': 'middle'},
            {'id': 'reviewed'},
            {'id': 'oldest'},
        ]

        result = _get_new_commits_since_last_review(commits, 'reviewed')

        self.assertEqual([commit['id'] for commit in result], ['newest', 'middle'])

    def test_build_review_commits_text_excludes_mainline_sync_commits(self):
        commits = [
            {
                'message': "Merge branch 'main' into feature/demo\n",
                'title': "Merge branch 'main' into feature/demo",
            },
            {
                'message': 'feat: keep this commit\n',
                'title': 'feat: keep this commit',
            },
        ]

        commits_text = _build_review_commits_text(
            commits,
            source_branch='feature/demo',
            target_branch='main',
            is_incremental=True,
        )

        self.assertEqual(commits_text, f'{_INCREMENTAL_REVIEW_PREFIX}feat: keep this commit')

    @patch('biz.queue.worker.ReviewService.get_last_mr_review_commit_id', return_value='reviewed')
    @patch('biz.queue.worker.ReviewService.check_mr_last_commit_id_exists', return_value=False)
    @patch('biz.queue.worker.MergeRequestHandler')
    def test_handle_merge_request_event_skips_mainline_sync_only_increment(self, mock_handler_cls, *_mocks):
        handler = MagicMock()
        handler.action = 'update'
        handler.get_merge_request_commits.return_value = [
            {
                'id': 'merge-main',
                'title': "Merge branch 'main' into feature/demo",
                'message': "Merge branch 'main' into feature/demo\n",
            },
            {
                'id': 'reviewed',
                'title': 'feat: original change',
                'message': 'feat: original change\n',
            },
        ]
        mock_handler_cls.return_value = handler

        webhook_data = {
            'project': {'name': 'demo'},
            'user': {'username': 'alice', 'name': 'Alice'},
            'object_attributes': {
                'action': 'update',
                'source_branch': 'feature/demo',
                'target_branch': 'main',
                'iid': 1,
                'last_commit': {'id': 'merge-main'},
            },
        }

        handle_merge_request_event(webhook_data, 'token', 'https://gitlab.example.com', 'gitlab')

        handler.repository_compare.assert_not_called()
        handler.get_merge_request_changes.assert_not_called()

    @patch('biz.queue.worker.ReviewService.get_last_mr_review_commit_id', return_value='reviewed')
    @patch('biz.queue.worker.ReviewService.check_mr_last_commit_id_exists', return_value=False)
    @patch.dict('os.environ', {'GITLAB_BOT_USERNAME': 'bot'}, clear=False)
    @patch('biz.queue.worker.NoteHandler')
    def test_handle_note_event_skips_mainline_sync_only_increment(self, mock_handler_cls, *_mocks):
        handler = MagicMock()
        handler.noteable_type = 'MergeRequest'
        handler.merge_request_iid = 1
        handler.action = 'create'
        handler.author_username = 'alice'
        handler.note = '@bot'
        handler.source_branch = 'feature/demo'
        handler.target_branch = 'main'
        handler.last_commit_id = 'merge-main'
        handler.get_merge_request_commits.return_value = [
            {
                'id': 'merge-main',
                'title': "Merge branch 'main' into feature/demo",
                'message': "Merge branch 'main' into feature/demo\n",
            },
            {
                'id': 'reviewed',
                'title': 'feat: original change',
                'message': 'feat: original change\n',
            },
        ]
        mock_handler_cls.return_value = handler

        webhook_data = {
            'project': {'name': 'demo'},
            'user': {'username': 'alice', 'name': 'Alice'},
            'object_attributes': {
                'action': 'create',
                'note': '@bot',
            },
        }

        handle_note_event(webhook_data, 'token', 'https://gitlab.example.com', 'gitlab')

        handler.repository_compare.assert_not_called()
        handler.add_merge_request_note.assert_called_once_with('当前 MR 自上次审核后仅合入了 main/master 的同步提交，无需重复审核。')
