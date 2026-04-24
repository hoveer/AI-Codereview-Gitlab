#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for MrChatReviewer — verifying that diffs/commits context is included
in the user prompt even when those sections are guarded by Jinja conditionals."""
from unittest import TestCase, main
from unittest.mock import MagicMock, patch


class TestMrChatReviewerPromptRendering(TestCase):
    """Test that MrChatReviewer.chat() correctly includes MR context in the prompt."""

    def _make_reviewer(self):
        """Build a MrChatReviewer whose LLM client is mocked."""
        with patch('biz.utils.code_reviewer.Factory') as MockFactory:
            mock_client = MagicMock()
            mock_client.completions.return_value = 'mocked reply'
            MockFactory.return_value.getClient.return_value = mock_client

            from biz.utils.code_reviewer import MrChatReviewer
            reviewer = MrChatReviewer()
            reviewer.client = mock_client
            return reviewer, mock_client

    def test_diffs_included_when_provided(self):
        """When diffs_text is non-empty it should appear in the user message sent to LLM."""
        reviewer, mock_client = self._make_reviewer()
        reviewer.chat(
            user_question='What does this change do?',
            diffs_text='- old line\n+ new line',
            commits_text='',
        )
        call_args = mock_client.completions.call_args
        messages = call_args[1].get('messages') or call_args[0][0]
        user_content = next(m['content'] for m in messages if m['role'] == 'user')
        self.assertIn('- old line\n+ new line', user_content)

    def test_diffs_absent_when_empty(self):
        """When diffs_text is empty the diffs section should not appear in the user message."""
        reviewer, mock_client = self._make_reviewer()
        reviewer.chat(
            user_question='General question',
            diffs_text='',
            commits_text='',
        )
        call_args = mock_client.completions.call_args
        messages = call_args[1].get('messages') or call_args[0][0]
        user_content = next(m['content'] for m in messages if m['role'] == 'user')
        self.assertNotIn('代码变更内容', user_content)

    def test_commits_included_when_provided(self):
        """When commits_text is non-empty it should appear in the user message."""
        reviewer, mock_client = self._make_reviewer()
        reviewer.chat(
            user_question='Summarise the commits',
            diffs_text='',
            commits_text='fix: correct typo in README',
        )
        call_args = mock_client.completions.call_args
        messages = call_args[1].get('messages') or call_args[0][0]
        user_content = next(m['content'] for m in messages if m['role'] == 'user')
        self.assertIn('fix: correct typo in README', user_content)

    def test_commits_absent_when_empty(self):
        """When commits_text is empty the commits section should not appear in the user message."""
        reviewer, mock_client = self._make_reviewer()
        reviewer.chat(
            user_question='General question',
            diffs_text='',
            commits_text='',
        )
        call_args = mock_client.completions.call_args
        messages = call_args[1].get('messages') or call_args[0][0]
        user_content = next(m['content'] for m in messages if m['role'] == 'user')
        self.assertNotIn('提交历史', user_content)

    def test_user_question_always_included(self):
        """The user question should always appear in the prompt regardless of context."""
        reviewer, mock_client = self._make_reviewer()
        reviewer.chat(
            user_question='Is this code thread-safe?',
            diffs_text='',
            commits_text='',
        )
        call_args = mock_client.completions.call_args
        messages = call_args[1].get('messages') or call_args[0][0]
        user_content = next(m['content'] for m in messages if m['role'] == 'user')
        self.assertIn('Is this code thread-safe?', user_content)


if __name__ == '__main__':
    main()
