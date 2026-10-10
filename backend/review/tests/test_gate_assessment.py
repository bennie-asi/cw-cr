from unittest.mock import patch

from django.test import TestCase

from review.models import LineComment, Project, ProjectPolicy, Review
from review.services.gate_assessment import assess_review


class GateAssessmentTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='gate', gitlab_project_path='qa/gate')
        self.policy = ProjectPolicy.objects.create(project=self.project)
        self.review = Review.objects.create(
            project=self.project, mr_iid=42, status='completed',
            summary='中文总评', summary_sync_status='synced', head_sha='a' * 40,
        )
        self.remote = {
            'iid': 42, 'sha': 'a' * 40, 'state': 'opened', 'draft': False,
            'work_in_progress': False, 'has_conflicts': False,
            'detailed_merge_status': 'mergeable',
        }
        sentinel = patch('requests.sessions.Session.request', side_effect=AssertionError('No network in assessment service'))
        sentinel.start()
        self.addCleanup(sentinel.stop)

    def assess(self):
        return assess_review(self.review, self.policy, self.remote)

    def assertBlocked(self, key):
        result = self.assess()
        self.assertFalse(result['passed'])
        self.assertFalse(result['checks'][key])
        self.assertTrue(result['reason'])
        return result

    def test_success_is_bound_to_review_and_sha_without_writes(self):
        before = Review.objects.values().get(pk=self.review.pk)
        policy_before = ProjectPolicy.objects.values().get(pk=self.policy.pk)
        result = self.assess()
        self.assertTrue(result['passed'])
        self.assertTrue(all(result['checks'].values()))
        self.assertEqual(result['project_id'], str(self.project.pk))
        self.assertEqual(result['review_id'], str(self.review.pk))
        self.assertEqual(result['mr_iid'], 42)
        self.assertEqual(result['reviewed_sha'], 'a' * 40)
        self.assertEqual(result['current_sha'], 'a' * 40)
        self.assertEqual(result['blocking_count'], 0)
        self.assertEqual(result['blocking_levels'], ['P0', 'P1', 'UNRATED'])
        self.assertEqual(Review.objects.values().get(pk=self.review.pk), before)
        self.assertEqual(ProjectPolicy.objects.values().get(pk=self.policy.pk), policy_before)

    def test_foreign_policy_is_rejected(self):
        other = Project.objects.create(name='other', gitlab_project_path='qa/other')
        policy = ProjectPolicy.objects.create(project=other)
        with self.assertRaisesMessage(ValueError, '项目'):
            assess_review(self.review, policy, self.remote)

    def test_project_and_review_readiness(self):
        self.project.enabled = False
        self.assertBlocked('project_enabled')
        self.project.enabled = True
        for state in ('queued', 'running', 'failed'):
            with self.subTest(state=state):
                self.review.status = state
                self.assertBlocked('review_ready')
        self.review.status = 'blocked'
        self.assertTrue(self.assess()['passed'])

    def test_summary_must_exist_and_be_synced(self):
        self.review.summary = '  \n'
        self.assertBlocked('summary_ready')
        self.review.summary = '中文总评'
        for state in ('pending', 'failed', ''):
            with self.subTest(state=state):
                self.review.summary_sync_status = state
                self.assertBlocked('summary_ready')

    def test_latest_review_is_scoped_to_same_mr(self):
        Review.objects.create(project=self.project, mr_iid=43)
        self.assertTrue(self.assess()['passed'])
        Review.objects.create(project=self.project, mr_iid=42)
        self.assertBlocked('latest_review')

    def test_blocker_levels_follow_policy_and_resolved_state(self):
        comment = LineComment.objects.create(
            review=self.review, file_path='main.py', new_line=1,
            title='问题', body='说明', level='P0', status='open',
        )
        for state in ('open', 'published', 'failed'):
            comment.status = state
            comment.save()
            self.assertEqual(self.assertBlocked('blockers_resolved')['blocking_count'], 1)
        comment.status = 'resolved'
        comment.save()
        self.assertTrue(self.assess()['passed'])
        comment.status = 'open'
        comment.level = 'P1'
        comment.save()
        self.assertBlocked('blockers_resolved')
        self.policy.block_on_p1 = False
        self.assertTrue(self.assess()['passed'])
        self.assertEqual(self.assess()['blocking_levels'], ['P0', 'UNRATED'])
        comment.level = 'P0'
        comment.save()
        self.assertBlocked('blockers_resolved')
        comment.level = 'P2'
        comment.save()
        self.assertTrue(self.assess()['passed'])
        comment.level = 'UNRATED'
        comment.save()
        self.assertBlocked('blockers_resolved')

    def test_missing_malformed_or_changed_sha_blocks(self):
        for sha in ('', None, 42, [], 'b' * 40):
            with self.subTest(sha=sha):
                self.remote['sha'] = sha
                result = self.assertBlocked('sha_matches')
                self.assertIsInstance(result['current_sha'], str)
        self.remote.pop('sha')
        self.assertBlocked('sha_matches')
        self.remote['sha'] = 'a' * 40
        self.review.head_sha = ''
        self.assertBlocked('sha_matches')

    def test_remote_mr_identity_must_match(self):
        for iid in (43, '42', None, True):
            with self.subTest(iid=iid):
                self.remote['iid'] = iid
                self.assertBlocked('correct_mr')
        self.review.mr_iid = 1
        self.remote['iid'] = True
        self.assertBlocked('correct_mr')

    def test_gitlab_checks_fail_closed(self):
        for key, value in (
            ('state', 'closed'), ('state', 'merged'), ('state', None),
            ('draft', True), ('work_in_progress', True), ('has_conflicts', True),
            ('draft', None), ('draft', 0), ('has_conflicts', None),
            ('detailed_merge_status', 'checking'), ('detailed_merge_status', 'ci_must_pass'),
            ('detailed_merge_status', None), ('detailed_merge_status', 'unknown'),
        ):
            with self.subTest(key=key, value=value):
                original = self.remote[key]
                self.remote[key] = value
                self.assertBlocked('gitlab_ready')
                self.remote[key] = original
        self.remote.pop('detailed_merge_status')
        self.remote['merge_status'] = 'can_be_merged'
        self.assertBlocked('gitlab_ready')
        for key in ('draft', 'has_conflicts'):
            original = self.remote.pop(key)
            self.remote['detailed_merge_status'] = 'mergeable'
            self.assertBlocked('gitlab_ready')
            self.remote[key] = original
