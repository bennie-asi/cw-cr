from copy import deepcopy
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings

from review.models import LineComment, Project, ProjectPolicy, Review, ReviewJob


CSRF = 'assert first_session.request.call_args[1]["headers"]["X-Csrftoken"] is None'
DIFF = ('diff --git a/test_login.py b/test_login.py\n@@ -147,2 +157,3 @@\n'
        '-    assert result == expected\n-    assert flush.called\n'
        '+    assert result == changed\n+    assert flush.called\n+    ' + CSRF)


class FindingLocationTests(SimpleTestCase):
    def finding(self, **extra):
        return {'file_path': 'test_login.py', 'new_line': 157, 'body': f'`{CSRF}` 可能掩盖错误', **extra}

    def test_existing_but_wrong_line_is_relocated_by_original_code(self):
        from review.services.finding_locations import ground_finding_anchors
        source = self.finding(anchor_code=CSRF)
        result = ground_finding_anchors(DIFF, [source])[0]
        self.assertEqual(result['new_line'], 159)
        self.assertEqual(result['anchor_code'], '    ' + CSRF)
        self.assertEqual(source['new_line'], 157)

    def test_historical_first_quote_relocates_without_using_later_proposed_code(self):
        from review.services.finding_locations import finding_location
        result = finding_location(DIFF, self.finding(body=f'`{CSRF}` 建议改成 `assert result == changed`'))
        self.assertEqual(result['status'], 'relocated')
        self.assertEqual(result['new_line'], 159)
        self.assertEqual(result['original_new_line'], 157)

    def test_explicit_code_cannot_override_a_conflicting_full_source_quote(self):
        from review.services.finding_locations import ground_finding_anchors
        with self.assertRaisesRegex(ValueError, '正文引用不一致'):
            ground_finding_anchors(DIFF, [self.finding(anchor_code='assert result == changed')])

    def test_repeated_code_cannot_correct_a_wrong_line_by_proximity(self):
        from review.services.finding_locations import ground_finding_anchors, finding_location
        diff = DIFF.replace('+    assert flush.called', '+    ' + CSRF)
        finding = self.finding(anchor_code=CSRF)
        self.assertEqual(finding_location(diff, finding)['status'], 'unverified')
        with self.assertRaisesRegex(ValueError, '无法精确定位'):
            ground_finding_anchors(diff, [finding])

    def test_repeated_code_at_declared_matching_line_keeps_that_line(self):
        from review.services.finding_locations import ground_finding_anchors
        diff = DIFF.replace('+    assert flush.called', '+    ' + CSRF)
        self.assertEqual(ground_finding_anchors(diff, [self.finding(new_line=159, anchor_code=CSRF)])[0]['new_line'], 159)

    def test_literal_ellipsis_in_explicit_original_source_is_not_a_placeholder(self):
        from review.services.finding_locations import ground_finding_anchors
        diff = 'diff --git a/protocol.py b/protocol.py\n@@ -0,0 +1 @@\n+def run(self): ...'
        finding = {'file_path': 'protocol.py', 'new_line': 1, 'anchor_code': 'def run(self): ...'}
        self.assertEqual(ground_finding_anchors(diff, [finding])[0]['new_line'], 1)

    def test_missing_forged_abbreviated_and_suggested_evidence_never_falls_back_to_existing_line(self):
        from review.services.finding_locations import ground_finding_anchors, finding_location
        for extra in ({'body': '需要修复'}, {'anchor_code': 'invented()'},
                      {'body': '`assert ... is None` 建议 `' + CSRF + '`'},
                      {'body': '建议替换为 `' + CSRF + '`'},
                      {'body': '`missing_source()` 建议 `' + CSRF + '`'}):
            with self.subTest(extra=extra):
                finding = self.finding(**extra)
                self.assertEqual(finding_location(DIFF, finding)['status'], 'unverified')
                with self.assertRaises(ValueError):
                    ground_finding_anchors(DIFF, [finding])

    def test_deleted_side_and_rename_preserve_actual_side(self):
        from review.services.finding_locations import ground_finding_anchors
        diff = 'diff --git a/old.py b/new.py\n@@ -10,2 +20 @@\n-removed_one()\n-removed_two()\n+new_value()'
        result = ground_finding_anchors(diff, [{'file_path': 'new.py', 'old_line': 10, 'anchor_code': 'removed_two()'}])[0]
        self.assertEqual(result['old_line'], 11)
        self.assertIsNone(result.get('new_line'))
        with self.assertRaises(ValueError):
            ground_finding_anchors(diff, [{'file_path': 'new.py', 'new_line': 20, 'anchor_code': 'removed_two()'}])

    def test_other_file_is_not_used_to_relocate_and_multiple_hunks_keep_coordinates(self):
        from review.services.finding_locations import ground_finding_anchors
        diff = DIFF + '\n@@ -300 +302 @@\n-before()\n+other_hunk()'
        result = ground_finding_anchors(diff, [self.finding(anchor_code='other_hunk()', body='其他片段有误')])[0]
        self.assertEqual(result['new_line'], 302)
        with self.assertRaises(ValueError):
            ground_finding_anchors(diff, [self.finding(file_path='elsewhere.py', anchor_code=CSRF)])

    def test_annotated_model_input_has_file_coordinates_without_altering_stored_diff(self):
        from review.services.finding_locations import numbered_diff
        rendered = numbered_diff(DIFF)
        self.assertIn('test_login.py', rendered)
        self.assertIn('new=159', rendered)
        self.assertIn(CSRF, rendered)
        self.assertNotIn('new=159', DIFF)


class HistoricalLocationAPITests(TestCase):
    def test_serializer_corrects_display_without_modifying_published_record(self):
        from review.serializers import LineCommentSerializer
        project = Project.objects.create(name='anchors', gitlab_project_path='team/anchors')
        review = Review.objects.create(project=project, mr_iid=2201, diff_text=DIFF)
        comment = LineComment.objects.create(review=review, file_path='test_login.py', new_line=157,
            title='CSRF 断言', body=f'`{CSRF}` 可能掩盖错误', status=LineComment.Status.PUBLISHED, discussion_id='existing')
        data = LineCommentSerializer(comment).data
        self.assertEqual(data['location']['new_line'], 159)
        self.assertEqual(data['location']['status'], 'relocated')
        self.assertEqual(data['new_line'], 157)
        comment.refresh_from_db()
        self.assertEqual(comment.new_line, 157)
        self.assertEqual(comment.discussion_id, 'existing')

    def test_notification_preview_uses_verified_line_without_sending(self):
        from review.integrations.wecom import WeComClient
        project = Project.objects.create(name='anchors', gitlab_project_path='team/anchors')
        review = Review.objects.create(project=project, mr_iid=2201, diff_text=DIFF)
        LineComment.objects.create(review=review, file_path='test_login.py', new_line=157,
            title='CSRF 断言', body=f'`{CSRF}` 可能掩盖错误')
        self.assertIn('test_login.py:159', WeComClient.review_content(review))


@override_settings(REVIEW_ALLOW_TEST_FALLBACK=False, REVIEW_RETRY_BACKOFF_SECONDS=0, REVIEW_EXECUTE_INLINE=False)
class ProductionAnchorPipelineTests(TestCase):
    def setUp(self):
        from review.services.review_runner import create_review_job
        self.project = Project.objects.create(name='anchors', gitlab_project_path='team/anchors')
        ProjectPolicy.objects.create(project=self.project)
        self.result = {'summary': '需要修复 CSRF 断言。', 'findings': [{
            'file_path': 'test_login.py', 'new_line': 157, 'anchor_code': CSRF,
            'title': '断言有误', 'body': f'`{CSRF}` 可能掩盖传参错误', 'level': 'P1',
        }]}
        patches = [
            patch('review.services.review_runner.GitLabClient.fetch_merge_request', return_value={
                'title': '评审定位', 'diff_refs': {'base_sha': 'base', 'start_sha': 'start', 'head_sha': 'head'}}),
            patch('review.services.review_runner.GitLabClient.fetch_diff', return_value=DIFF),
            patch('review.services.review_runner.LLMClient.analyze', return_value=self.result),
            patch('review.services.review_runner.GitLabClient.create_discussion', return_value={'id': 'published'}),
            patch('review.services.review_runner.GitLabClient.create_summary_note', return_value={'id': 'summary'}),
            patch('review.services.review_runner.WeComClient.send_review', return_value={'ok': True}),
        ]
        self.fetch, self.diff, self.analyze, self.publish, self.summary, self.notify = [p.start() for p in patches]
        for p in patches:
            self.addCleanup(p.stop)
        self.job = create_review_job(self.project, {'mr_iid': 2201})

    def test_wrong_but_existing_line_is_corrected_and_snapshotted_before_publish(self):
        from review.services.review_runner import run_review_job
        seen = []
        def observe(review, comment):
            self.job.refresh_from_db()
            seen.append((comment.new_line, self.job.analysis_result['findings'][0]['new_line']))
            return {'id': 'published'}
        self.publish.side_effect = observe
        review = run_review_job(self.job.id)
        self.assertEqual(seen, [(159, 159)])
        self.assertEqual(review.comments.get().anchor_code, '    ' + CSRF)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, ReviewJob.Status.SUCCEEDED)

    def test_unverifiable_code_stops_all_publications_and_notifications(self):
        from review.services.review_runner import run_review_job
        self.result['findings'][0]['anchor_code'] = 'invented()'
        review = run_review_job(self.job.id)
        self.assertIn('无法精确定位', review.error_message)
        self.assertEqual(review.comments.count(), 0)
        self.publish.assert_not_called()
        self.summary.assert_not_called()
        self.notify.assert_not_called()

    def test_retry_reuses_corrected_anchor_without_duplicate_comments(self):
        from review.services.review_runner import run_review_job
        self.publish.side_effect = RuntimeError('temporary failure')
        run_review_job(self.job.id)
        self.publish.side_effect = None
        review = run_review_job(self.job.id)
        self.assertEqual(review.comments.count(), 1)
        self.assertEqual(review.comments.get().new_line, 159)
        self.assertEqual(self.analyze.call_count, 1)
        self.assertEqual(self.diff.call_count, 1)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, ReviewJob.Status.SUCCEEDED)

    def test_legacy_wrong_snapshot_with_existing_comments_never_creates_duplicate_publication(self):
        from review.services.review_runner import run_review_job
        self.publish.side_effect = RuntimeError('temporary failure')
        review = run_review_job(self.job.id)
        legacy = deepcopy(self.result)
        legacy['findings'][0]['new_line'] = 157
        self.job.refresh_from_db()
        self.job.analysis_result = legacy
        self.job.save()
        self.publish.reset_mock()
        review = run_review_job(self.job.id)
        self.assertIn('避免重复发布', review.error_message)
        self.assertEqual(review.comments.count(), 1)
        self.publish.assert_not_called()

    def test_relocated_anchor_never_guesses_a_suggestion_replacement_range(self):
        from review.services.review_runner import run_review_job
        self.result['findings'][0]['suggestion'] = {'start_line': 157, 'end_line': 157,
            'original_code': 'assert result == changed', 'replacement_code': 'assert result == correct'}
        review = run_review_job(self.job.id)
        self.assertIn('建议替换范围', review.error_message)
        self.publish.assert_not_called()
