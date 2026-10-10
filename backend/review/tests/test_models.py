from django.test import TestCase

from review.models import LineComment, Project, ProjectPolicy, Review


class ReviewModelTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name='cw-cr-demo',
            gitlab_project_path='demo/cw-cr-demo',
        )
        ProjectPolicy.objects.create(project=self.project)
        self.review = Review.objects.create(
            project=self.project,
            mr_iid=357,
            title='自动接入代码审查平台',
            source=Review.Source.WEBHOOK,
            status=Review.Status.COMPLETED,
        )

    def test_blocking_comment_count_only_includes_unresolved_p0_p1(self):
        LineComment.objects.create(
            review=self.review,
            file_path='src/service/review.ts',
            new_line=118,
            level=LineComment.Level.P1,
            title='异常路径会吞掉上下文',
            body='保留错误上下文并进入重试队列。',
            status=LineComment.Status.PUBLISHED,
        )
        LineComment.objects.create(
            review=self.review,
            file_path='src/types/review.ts',
            new_line=12,
            level=LineComment.Level.P2,
            title='可以抽取类型',
            body='建议复用已有类型。',
            status=LineComment.Status.OPEN,
        )
        LineComment.objects.create(
            review=self.review,
            file_path='src/service/webhook.ts',
            new_line=64,
            level=LineComment.Level.P1,
            title='幂等键',
            body='使用 event_uuid 做去重。',
            status=LineComment.Status.RESOLVED,
        )

        self.assertEqual(self.review.blocking_comment_count, 1)
        self.assertEqual(self.review.comments.filter(status=LineComment.Status.RESOLVED).count(), 1)

    def test_project_policy_has_chinese_prompt_default(self):
        self.assertIn('中文', self.project.policy.summary_prompt)
        self.assertEqual(self.project.policy.engine, 'builtin')
