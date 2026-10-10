from django.test import SimpleTestCase


class DiffPositionTests(SimpleTestCase):
    def test_model_parser_does_not_round_or_coerce_boolean_line_numbers(self):
        import json
        from review.integrations.llm import LLMClient
        for number in (True, 1.5, False):
            with self.subTest(number=number), self.assertRaises(ValueError):
                LLMClient()._parse_result(json.dumps({'summary': 'x', 'findings': [{'file_path': 'a.py', 'new_line': number}]}))

    def test_new_file_and_second_hunk_have_real_coordinates(self):
        from review.services.diff_positions import discussion_position
        diff = 'diff --git a/a.py b/a.py\n@@ -1 +1,2 @@\n one\n+two\n@@ -30 +31 @@\n-end\n+fixed\n\\ No newline at end of file'
        self.assertEqual(discussion_position(diff, 'a.py', new_line=31)['new_line'], 31)
        with self.assertRaises(ValueError):
            discussion_position(diff, 'a.py', new_line=30)
        created = 'diff --git a/new.py b/new.py\n@@ -0,0 +1 @@\n+hello'
        self.assertNotIn('old_line', discussion_position(created, 'new.py', new_line=1))

    def test_truncated_or_overlong_hunk_is_rejected(self):
        from review.services.diff_positions import diff_lines
        for diff in ('diff --git a/a b/a\n@@ -0,0 +1,2 @@\n+one',
                     'diff --git a/a b/a\n@@ -0,0 +1 @@\n+one\n+extra'):
            with self.subTest(diff=diff), self.assertRaises(ValueError):
                diff_lines(diff)

    def test_context_position_contains_both_shifted_coordinates(self):
        from review.services.diff_positions import discussion_position
        self.assertEqual(discussion_position(self.diff, 'new.py', new_line=21),
                         {'old_path': 'old.py', 'new_path': 'new.py', 'old_line': 11, 'new_line': 21})

    def test_added_and_deleted_positions_do_not_include_opposite_side(self):
        from review.services.diff_positions import discussion_position
        self.assertEqual(discussion_position(self.diff, 'new.py', new_line=20),
                         {'old_path': 'old.py', 'new_path': 'new.py', 'new_line': 20})
        self.assertEqual(discussion_position(self.diff, 'new.py', old_line=10),
                         {'old_path': 'old.py', 'new_path': 'new.py', 'old_line': 10})

    def test_inconsistent_dual_coordinates_are_rejected(self):
        from review.services.diff_positions import validate_findings
        with self.assertRaises(ValueError):
            validate_findings(self.diff, [{'file_path': 'new.py', 'new_line': 21, 'old_line': 10}])

    diff = 'diff --git a/old.py b/new.py\n--- a/old.py\n+++ b/new.py\n@@ -10,2 +20,2 @@\n-deleted\n+added\n context\n\\ No newline at end of file'

    def test_accepts_actual_old_new_and_context_lines(self):
        from review.services.diff_positions import validate_findings
        validate_findings(self.diff, [
            {'file_path': 'new.py', 'old_line': 10},
            {'file_path': 'new.py', 'new_line': 20},
            {'file_path': 'new.py', 'new_line': 21},
        ])

    def test_rejects_fabricated_or_wrong_side_locations(self):
        from review.services.diff_positions import validate_findings
        for finding in [
            {'file_path': 'missing.py', 'new_line': 20},
            {'file_path': 'new.py', 'new_line': 10},
            {'file_path': 'new.py', 'new_line': 22},
            {'file_path': 'new.py', 'new_line': True},
            {'file_path': 'new.py', 'new_line': 0},
        ]:
            with self.subTest(finding=finding), self.assertRaises(ValueError):
                validate_findings(self.diff, [finding])

    def test_suggestions_must_match_the_exact_hunk_before_publication(self):
        from review.services.diff_positions import validate_suggestions
        valid = {'file_path': 'new.py', 'new_line': 20, 'suggestion': {
            'start_line': 20, 'end_line': 20, 'original_code': 'added', 'replacement_code': 'fixed',
        }}
        validate_suggestions(self.diff, [valid])
        for invalid in (
            {**valid, 'suggestion': {**valid['suggestion'], 'original_code': 'wrong'}},
            {**valid, 'suggestion': {**valid['suggestion'], 'start_line': 10}},
            {**valid, 'suggestion': {**valid['suggestion'], 'replacement_code': 'added'}},
            {**valid, 'suggestion': {**valid['suggestion'], 'replacement_code': '```bad```'}},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_suggestions(self.diff, [invalid])

    def test_suggestion_original_is_grounded_from_the_verified_diff_range(self):
        from review.services.diff_positions import ground_suggestion_originals, validate_suggestions
        model_finding = {'file_path': 'new.py', 'new_line': 20, 'suggestion': {
            'start_line': 20, 'end_line': 20, 'original_code': 'model-echoed-wrong-code',
            'replacement_code': 'fixed',
        }}

        grounded = ground_suggestion_originals(self.diff, [model_finding])

        self.assertEqual(grounded[0]['suggestion']['original_code'], 'added')
        self.assertEqual(model_finding['suggestion']['original_code'], 'model-echoed-wrong-code')
        validate_suggestions(self.diff, grounded)
