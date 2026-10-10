from django.test import TestCase

from review.integrations.wecom import WeComClient
from review.models import LineComment, Project, Review


class NotificationConsistencyTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='通知项目', gitlab_project_path='team/notify')
        self.review = Review.objects.create(
            project=self.project, mr_iid=2201, status=Review.Status.BLOCKED,
            summary='本次变更质量较好，未发现阻断级问题。',
            title='修复单元测试', gitlab_url='https://gitlab.example/team/notify/-/merge_requests/2201',
            diff_text='diff --git a/test_login.py b/test_login.py\n@@ -18 +18 @@\n-old_csrf()\n+verify_csrf()',
        )
        self.comment = LineComment.objects.create(
            review=self.review, file_path='test_login.py', new_line=18,
            level='P1', title='CSRF 参数未验证', body='请验证真实参数。', status=LineComment.Status.PUBLISHED,
            anchor_code='verify_csrf()',
        )

    def test_notification_uses_actual_blockers_instead_of_contradictory_model_text(self):
        content = WeComClient.review_content(self.review)
        self.assertIn('结论：**需要修改**', content)
        self.assertIn('阻断问题：1 条', content)
        self.assertIn('CSRF 参数未验证', content)
        self.assertIn('test_login.py:18', content)
        self.assertNotIn('未发现阻断级问题', content)

    def test_resolved_comment_does_not_remain_a_blocker(self):
        self.comment.status = LineComment.Status.RESOLVED
        self.comment.save()
        content = WeComClient.review_content(self.review)
        self.assertIn('阻断问题：0 条', content)
        self.assertIn('已解决', content)
        self.assertNotIn('需要修改', content)

    def test_unrated_issue_requires_confirmation(self):
        self.comment.level = 'UNRATED'
        self.comment.save()
        content = WeComClient.review_content(self.review)
        self.assertIn('未评级', content)
        self.assertIn('需人工确认', content)
        self.assertIn('阻断问题：1 条', content)

    def test_failed_review_is_never_reported_as_passed(self):
        self.review.comments.all().delete()
        self.review.status = Review.Status.FAILED
        self.review.save()
        content = WeComClient.review_content(self.review)
        self.assertIn('评审失败', content)
        self.assertNotIn('结论：**通过**', content)

    def test_long_issue_digest_retains_header_and_link_within_utf8_budget(self):
        self.comment.title = '中' * 5000
        self.comment.save()
        content = WeComClient.review_content(self.review)
        self.assertLessEqual(len(content.encode('utf-8')), 3500)
        self.assertIn('阻断问题：1 条', content)
        self.assertIn(self.review.gitlab_url, content)
