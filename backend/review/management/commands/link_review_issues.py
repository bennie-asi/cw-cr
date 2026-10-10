"""Conservatively deduplicate historical MR issues without external requests."""
from django.core.management.base import BaseCommand
from django.db import transaction

from review.models import LineComment, Review
from review.services.mr_issues import enroll_legacy


class Command(BaseCommand):
    help = '关联历史评审中的重复 MR 问题，保留所有原评论和代码位置；不发送 GitLab 或通知请求'

    def handle(self, *args, **options):
        scopes = list(LineComment.objects.filter(issue__isnull=True).order_by().values(
            'review__project_id', 'review__repository_path', 'review__mr_iid').distinct())
        before = LineComment.objects.filter(issue__isnull=True).count()
        for scope in scopes:
            review = Review.objects.filter(project_id=scope['review__project_id'], mr_iid=scope['review__mr_iid'],
                repository_path=scope['review__repository_path']).select_related('project').order_by('-created_at').first()
            if review:
                with transaction.atomic():
                    enroll_legacy(review)
        linked = before - LineComment.objects.filter(issue__isnull=True).count()
        self.stdout.write(self.style.SUCCESS(f'已关联 {linked} 条历史评论，处理 {len(scopes)} 个 MR 范围；外部请求 0 次'))
