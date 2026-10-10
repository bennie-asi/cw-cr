from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from review.models import AuditLog, LineComment, Project, Review
from review.tests.auth_helpers import admin_client


class DashboardNotificationFilterTests(TestCase):
    def setUp(self):
        self.client = admin_client()
        self.first = Project.objects.create(name='api', gitlab_project_path='team/api')
        self.second = Project.objects.create(name='ui', gitlab_project_path='team/ui')

    def test_audit_history_filters_exact_project(self):
        own = AuditLog.objects.create(project=self.first, action='发送企业微信评审通知')
        AuditLog.objects.create(project=self.second, action='发送企业微信评审通知')
        response = self.client.get('/api/audit-logs/', {'project': str(self.first.pk)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row['id'] for row in response.data], [own.pk])

    def test_notification_history_filters_before_limit_for_platform_and_project(self):
        expected = AuditLog.objects.create(project=self.first, action='自动合并结果', metadata={'notification': {'status': 'done', 'ok': True}})
        AuditLog.objects.bulk_create([
            AuditLog(project=self.first, action=f'配置事件 {index}') for index in range(105)
        ])
        for url in ('/api/audit-logs/', f'/api/projects/{self.first.pk}/audit-logs/'):
            response = self.client.get(url, {'category': 'notification'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual([row['id'] for row in response.data], [expected.pk])

    def test_overview_reports_completed_and_blocked_series_separately(self):
        now = timezone.now()
        completed = Review.objects.create(project=self.first, mr_iid=1, status=Review.Status.COMPLETED, completed_at=now)
        blocked = Review.objects.create(project=self.second, mr_iid=2, status=Review.Status.BLOCKED, completed_at=now)
        LineComment.objects.create(review=blocked, level='P1', file_path='main.py', new_line=1, title='未解决阻断', body='中文说明')
        Review.objects.filter(pk__in=(completed.pk, blocked.pk)).update(completed_at=now - timedelta(days=1))
        response = self.client.get('/api/overview/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['daily_completed'][-2], 1)
        self.assertEqual(response.data['daily_blocked'][-2], 1)
        self.assertEqual(response.data['daily_reviews'][-2], 2)

    def test_project_notification_preview_uses_actual_review_findings(self):
        review = Review.objects.create(
            project=self.first, mr_iid=35, status=Review.Status.COMPLETED,
            summary='真实的中文评审结论。', gitlab_url='https://gitlab.example/team/api/-/merge_requests/35',
        )
        response = self.client.get(f'/api/projects/{self.first.pk}/notification-preview/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['review_id'], str(review.pk))
        self.assertIn('当前没有未解决的行级问题', response.data['content'])
        self.assertIn('结论：**通过**', response.data['content'])
        self.assertIn('merge_requests/35', response.data['content'])
        self.assertEqual(self.client.get(f'/api/projects/{self.second.pk}/notification-preview/', {'review': str(review.pk)}).status_code, 404)
