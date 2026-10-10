from django.test import SimpleTestCase


class SuggestionTests(SimpleTestCase):
    diff = 'diff --git a/old.py b/new.py\n@@ -10,2 +20,3 @@\n-old\n+    first\n+    second\n context\n\\ No newline at end of file\n@@ -50 +60 @@\n-end\n+last'

    def render(self, suggestion, new_line=21, body='中文说明', old_line=None):
        from review.services.suggestions import suggestion_body
        return suggestion_body(self.diff, 'new.py', new_line, old_line, body, suggestion)

    def test_multi_line_range_is_relative_to_comment_anchor(self):
        body = self.render({'start_line': 20, 'end_line': 22, 'original_code': '    first\n    second\ncontext', 'replacement_code': '    fixed'})
        self.assertIn('```suggestion:-1+1\n    fixed\n```', body)

    def test_single_line_and_empty_replacement(self):
        body = self.render({'start_line': 21, 'end_line': 21, 'original_code': '    second', 'replacement_code': ''})
        self.assertIn('```suggestion:-0+0\n\n```', body)

    def test_second_hunk_and_terminal_no_newline_do_not_shift_range(self):
        body = self.render({'start_line': 60, 'end_line': 60, 'original_code': 'last', 'replacement_code': 'fixed'}, new_line=60)
        self.assertIn('```suggestion:-0+0\nfixed\n```', body)

    def test_range_limits_are_checked_before_large_iteration(self):
        body = self.render({'start_line': 1, 'end_line': 1000000000, 'original_code': '', 'replacement_code': 'fixed'})
        self.assertNotIn('```suggestion', body)

    def test_invalid_suggestion_keeps_comment_without_apply_block(self):
        valid = {'start_line': 20, 'end_line': 21, 'original_code': '    first\n    second', 'replacement_code': 'fixed'}
        for change in [{'original_code': 'first\nsecond'}, {'end_line': 60}, {'start_line': 22}, {'start_line': True}, {'replacement_code': None}, {'replacement_code': valid['original_code']}]:
            with self.subTest(change=change):
                result = self.render({**valid, **change})
                self.assertIn('中文说明', result)
                self.assertNotIn('```suggestion', result)

    def test_deleted_side_cannot_supply_apply_block(self):
        result = self.render({'start_line': 10, 'end_line': 10, 'original_code': 'old', 'replacement_code': 'fixed'}, new_line=None, old_line=10)
        self.assertNotIn('```suggestion', result)

    def test_raw_unvalidated_suggestion_in_body_is_neutralized(self):
        self.assertNotIn('```suggestion', self.render(None, body='说明\n```suggestion:-100+100\nevil\n```'))

    def test_replacement_with_code_fence_is_not_executable_on_older_gitlab(self):
        result = self.render({'start_line': 21, 'end_line': 21, 'original_code': '    second', 'replacement_code': '```suggestion\nevil\n```'})
        self.assertNotIn('```suggestion', result)
