from unittest.mock import Mock, patch

import requests
from django.test import TestCase, override_settings

from review.integrations.gitlab import GitLabClient
from review.models import Project, Review, LineComment


def response(data, next_page=''):
    result = Mock()
    result.json.return_value = data
    result.headers = {'X-Next-Page': next_page}
    return result


@override_settings(GITLAB_TOKEN='test', REVIEW_ALLOW_TEST_FALLBACK=False)
class PublicationRecoveryTests(TestCase):
    @patch('review.integrations.gitlab.requests.get')
    @patch('review.integrations.gitlab.requests.post')
    def test_manual_publication_rejects_missing_or_wrong_code_before_post(self, post, get):
        get.return_value = response([])
        for code in ('', 'invented()'):
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, '无法精确定位'):
                self.comment.anchor_code = code
                self.client.create_discussion(self.review, self.comment)
        post.assert_not_called()

    @patch('review.integrations.gitlab.requests.get')
    @patch('review.integrations.gitlab.requests.post')
    def test_manual_publication_does_not_silently_post_a_different_historical_position(self, post, get):
        get.return_value = response([])
        self.review.diff_text = 'diff --git a/a.py b/a.py\n@@ -0,0 +1,2 @@\n+other\n+added'
        with self.assertRaisesRegex(ValueError, '坐标与引用代码不符'):
            self.client.create_discussion(self.review, self.comment)
        post.assert_not_called()

    @patch('review.integrations.gitlab.requests.get')
    @patch('review.integrations.gitlab.requests.post')
    def test_context_suggestion_posts_both_coordinates_and_relative_range(self, post, get):
        get.return_value = response([])
        post.return_value = response({'id': 'suggestion'})
        self.review.diff_text = 'diff --git a/old.py b/a.py\n@@ -10,2 +20,3 @@\n-old\n+first\n+second\n context'
        self.comment.new_line = 22
        self.comment.anchor_code = 'context'
        self.comment.suggestion = {'start_line': 20, 'end_line': 22, 'original_code': 'first\nsecond\ncontext', 'replacement_code': 'fixed'}
        self.client.create_discussion(self.review, self.comment)
        payload = post.call_args.kwargs['json']
        self.assertEqual(payload['position']['old_path'], 'old.py')
        self.assertEqual(payload['position']['old_line'], 11)
        self.assertEqual(payload['position']['new_line'], 22)
        self.assertIn('```suggestion:-2+0\nfixed\n```', payload['body'])

    @patch('review.views.GitLabClient.create_summary_note', side_effect=requests.Timeout('offline'))
    def test_summary_sync_failure_is_returned_and_persisted(self, send):
        from review.tests.auth_helpers import admin_client
        client = admin_client()
        result = client.patch(f'/api/reviews/{self.review.pk}/summary/', {'summary': '更新总评'}, format='json')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json().get('summary_sync_status'), 'failed')
        detail = client.get(f'/api/reviews/{self.review.pk}/')
        self.assertEqual(detail.json().get('summary_sync_status'), 'failed')

    @patch('review.integrations.gitlab.requests.put')
    @patch('review.integrations.gitlab.requests.get')
    @patch('review.integrations.gitlab.requests.post')
    def test_manual_summary_edit_updates_existing_note(self, post, get, put):
        get.return_value = response([{'id': 99, 'body': f'旧总评\n<!-- cw-cr:summary:{self.review.pk} -->'}])
        put.return_value = response({'id': 99})
        self.client.create_summary_note(self.review, update_existing=True)
        self.assertTrue(put.call_args.args[0].endswith('/notes/99'))
        self.assertIn('中文总评', put.call_args.kwargs['json']['body'])
        post.assert_not_called()

    def setUp(self):
        project = Project.objects.create(name='test', gitlab_url='https://gitlab.example', gitlab_project_path='team/test')
        self.review = Review.objects.create(project=project, mr_iid=7, base_sha='base', start_sha='start', head_sha='head', summary='中文总评', diff_text='diff --git a/a.py b/a.py\n@@ -0,0 +1 @@\n+added')
        self.comment = LineComment.objects.create(review=self.review, file_path='a.py', new_line=1, title='问题', body='说明', anchor_code='added')
        self.client = GitLabClient(project)
        fetch = patch('review.integrations.gitlab.GitLabClient.fetch_merge_request', return_value={'diff_refs': {'base_sha': 'base', 'start_sha': 'start', 'head_sha': 'head'}})
        self.fetch_mr = fetch.start()
        self.addCleanup(fetch.stop)

    @patch('review.integrations.gitlab.requests.get')
    @patch('review.integrations.gitlab.requests.post')
    def test_manual_publication_rejects_stale_commit_before_post(self, post, get):
        get.return_value = response([])
        self.fetch_mr.return_value = {'diff_refs': {'base_sha': 'base', 'start_sha': 'start', 'head_sha': 'changed'}}
        with self.assertRaises(ValueError):
            self.client.create_discussion(self.review, self.comment)
        post.assert_not_called()

    @patch('review.views.GitLabClient.create_discussion', return_value={'id': 'existing', 'resolved': True})
    def test_manual_publication_preserves_recovered_resolution(self, create):
        from review.tests.auth_helpers import admin_client
        result = admin_client().post(f'/api/reviews/{self.review.pk}/comments/{self.comment.pk}/publish/')
        self.assertEqual(result.status_code, 200)
        self.comment.refresh_from_db()
        self.assertEqual(self.comment.status, LineComment.Status.RESOLVED)

    @patch('review.integrations.gitlab.requests.get')
    @patch('review.integrations.gitlab.requests.post')
    def test_lost_response_is_recovered_without_second_post(self, post, get):
        remote = []
        get.side_effect = lambda *a, **kw: response(list(remote))
        def accepted_but_timed_out(*args, **kwargs):
            remote.append({'id': 'existing', 'notes': [{'body': kwargs['json']['body'], 'position': kwargs['json']['position']}]})
            raise requests.Timeout('response lost')
        post.side_effect = accepted_but_timed_out
        with self.assertRaises(requests.Timeout):
            self.client.create_discussion(self.review, self.comment)
        result = self.client.create_discussion(self.review, self.comment)
        self.assertEqual(result['id'], 'existing')
        self.assertEqual(post.call_count, 1)

    @patch('review.integrations.gitlab.requests.get')
    @patch('review.integrations.gitlab.requests.post')
    def test_summary_recovery_checks_later_pages(self, post, get):
        get.side_effect = [response([], '2'), response([{'id': 99, 'body': f'中文总评\n<!-- cw-cr:summary:{self.review.pk} -->'}])]
        result = self.client.create_summary_note(self.review)
        self.assertEqual(result['id'], 99)
        self.assertEqual(get.call_args.kwargs['params']['page'], 2)
        post.assert_not_called()

    @patch('review.integrations.gitlab.requests.get', side_effect=requests.Timeout('read failed'))
    @patch('review.integrations.gitlab.requests.post')
    def test_lookup_failure_never_blindly_posts(self, post, get):
        with self.assertRaises(requests.Timeout):
            self.client.create_discussion(self.review, self.comment)
        post.assert_not_called()

    @patch('review.integrations.gitlab.requests.get')
    @patch('review.integrations.gitlab.requests.post')
    def test_malformed_listing_never_blindly_posts(self, post, get):
        get.return_value = response({'error': 'unexpected'})
        with self.assertRaises(ValueError):
            self.client.create_summary_note(self.review)
        post.assert_not_called()

    @patch('review.integrations.gitlab.requests.get')
    @patch('review.integrations.gitlab.requests.post')
    def test_recovered_discussion_carries_matching_note_resolution(self, post, get):
        get.return_value = response([{'id': 'found', 'notes': [{
            'body': f'说明\n<!-- cw-cr:comment:{self.comment.pk} -->', 'resolved': True,
        }]}])
        result = self.client.create_discussion(self.review, self.comment)
        self.assertTrue(result.get('resolved'))
        post.assert_not_called()
