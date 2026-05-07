from unittest import TestCase
from unittest.mock import Mock, patch

from biz.utils.review_filter import (
    IGNORE_MAINLINE_MERGE_REBASE_CHANGES_ENABLED,
    filter_out_mainline_sync_commits,
    filter_out_mainline_changes,
    is_mainline_sync_commit,
)


class TestReviewFilter(TestCase):
    def test_config_disabled_keeps_existing_changes(self):
        review_changes = [{
            'new_path': 'service.py',
            'diff': '@@ -1 +1 @@\n-old\n+new',
            'additions': 1,
            'deletions': 1,
        }]
        compare_fn = Mock(side_effect=AssertionError('compare should not be called when filter is disabled'))

        with patch.dict('os.environ', {IGNORE_MAINLINE_MERGE_REBASE_CHANGES_ENABLED: '0'}, clear=False):
            result = filter_out_mainline_changes(
                review_changes,
                source_branch='feature/demo',
                target_branch='release',
                compare_fn=compare_fn,
                change_filter_fn=lambda changes: changes,
            )

        self.assertEqual(result, review_changes)
        compare_fn.assert_not_called()

    def test_enabled_filter_removes_mainline_only_changes(self):
        review_changes = [
            {
                'new_path': 'app.py',
                'diff': '@@ -1 +1 @@\n-old\n+mainline',
                'additions': 1,
                'deletions': 1,
            },
            {
                'new_path': 'feature.py',
                'diff': '@@ -10 +10 @@\n-old\n+feature',
                'additions': 1,
                'deletions': 1,
            },
        ]

        def compare_fn(base_ref, head_ref):
            self.assertEqual(base_ref, 'release')
            if head_ref == 'main':
                return [{
                    'new_path': 'app.py',
                    'diff': '@@ -1 +1 @@\n-old\n+mainline',
                    'additions': 1,
                    'deletions': 1,
                }]
            return []

        result = filter_out_mainline_changes(
            review_changes,
            source_branch='feature/demo',
            target_branch='release',
            compare_fn=compare_fn,
            change_filter_fn=lambda changes: changes,
            enabled=True,
        )

        self.assertEqual(result, [review_changes[1]])

    def test_enabled_filter_keeps_branch_specific_hunks(self):
        review_changes = [{
            'new_path': 'app.py',
            'diff': '\n'.join([
                '@@ -1 +1 @@',
                '-old',
                '+mainline',
                '@@ -20 +20 @@',
                '-legacy',
                '+feature',
            ]),
            'additions': 2,
            'deletions': 2,
        }]

        result = filter_out_mainline_changes(
            review_changes,
            source_branch='feature/demo',
            target_branch='release',
            compare_fn=lambda _base, head: [{
                'new_path': 'app.py',
                'diff': '@@ -1 +1 @@\n-old\n+mainline',
                'additions': 1,
                'deletions': 1,
            }] if head == 'main' else [],
            change_filter_fn=lambda changes: changes,
            enabled=True,
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['new_path'], 'app.py')
        self.assertEqual(result[0]['diff'], '@@ -20 +20 @@\n-legacy\n+feature')
        self.assertEqual(result[0]['additions'], 1)
        self.assertEqual(result[0]['deletions'], 1)

    def test_mainline_sync_commit_detected_from_message_when_parent_ids_missing(self):
        commit = {
            'parent_ids': [],
            'title': "Merge branch 'main' into feature/demo",
            'message': "Merge branch 'main' into feature/demo\n",
        }

        self.assertTrue(is_mainline_sync_commit(commit, source_branch='feature/demo', target_branch='main'))

    def test_mainline_sync_commit_filter_keeps_feature_commit(self):
        commits = [
            {
                'id': 'merge-main',
                'title': "Merge branch 'main' into feature/demo",
                'message': "Merge branch 'main' into feature/demo\n",
            },
            {
                'id': 'feature-change',
                'title': 'feat: update review flow',
                'message': 'feat: update review flow\n',
            },
        ]

        filtered = filter_out_mainline_sync_commits(
            commits,
            source_branch='feature/demo',
            target_branch='main',
        )

        self.assertEqual([commit['id'] for commit in filtered], ['feature-change'])

    def test_merge_into_main_is_not_mistaken_for_mainline_sync_commit(self):
        commit = {
            'title': "Merge branch 'feature/demo' into 'main'",
            'message': "Merge branch 'feature/demo' into 'main'\n",
        }

        self.assertFalse(is_mainline_sync_commit(commit, source_branch='feature/demo', target_branch='main'))
