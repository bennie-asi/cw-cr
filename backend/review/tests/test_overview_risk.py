from datetime import timedelta
from django.utils import timezone

from django.test import TestCase
from review.models import LineComment, Project, Review
from review.tests.auth_helpers import admin_client


class OverviewRiskTests(TestCase):
    def test_recent_severe_issues_are_counted_and_linked_to_review(self):
        client = admin_client()
        project = Project.objects.create(name='交易服务', gitlab_project_path='team/order')
        review = Review.objects.create(project=project, mr_iid=128, title='优化订单查询', status=Review.Status.BLOCKED)
        recent = LineComment.objects.create(review=review, file_path='orders.py', new_line=24, level='P1', title='缺少权限校验', body='需要校验')
        old = LineComment.objects.create(review=review, file_path='orders.py', new_line=30, level='P0', title='旧问题', body='旧问题')
        LineComment.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=9))
        response = client.get('/api/overview/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['recent_severe_count'], 1)
        self.assertEqual(response.json()['recent_severe_issues'][0]['review_id'], str(review.pk))
        self.assertEqual(response.json()['recent_severe_issues'][0]['title'], recent.title)
        self.assertEqual(len(response.json()['daily_severe_issues']), 7)
