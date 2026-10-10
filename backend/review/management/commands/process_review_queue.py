import time

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from django.db.models import F, Q
from django.utils import timezone

from review.models import ReviewJob
from review.services.auto_merge import reconcile_pending_merges
from review.services.review_runner import run_review_job


class Command(BaseCommand):
    help = '处理排队中的代码评审任务；生产环境可由 Celery/定时器调用。'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=10)
        parser.add_argument('--once', action='store_true', help='只处理一条任务')
        parser.add_argument('--watch', action='store_true', help='持续消费队列并恢复中断任务')
        parser.add_argument('--interval', type=float, default=2.0)

    def handle(self, *args, **options):
        if options['interval'] <= 0 or options['limit'] <= 0:
            raise CommandError('interval 和 limit 必须为正数')
        try:
            while True:
                close_old_connections()
                self.process_batch(options)
                if not options.get('watch') or options.get('once'):
                    break
                time.sleep(options['interval'])
        except KeyboardInterrupt:
            self.stdout.write('worker stopped')

    def process_batch(self, options):
        limit = 1 if options.get('once') else options['limit']
        now = timezone.now()
        jobs = ReviewJob.objects.filter(
            status__in=[ReviewJob.Status.QUEUED, ReviewJob.Status.RUNNING],
            project__enabled=True,
        ).filter(
            Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now),
        ).filter(
            Q(status=ReviewJob.Status.RUNNING) | Q(attempts__lt=F('max_attempts')),
        ).order_by('created_at')[:limit]
        count = 0
        for job in jobs:
            run_review_job(job.id, recover=True)
            count += 1
        count += reconcile_pending_merges(limit=limit)
        if count or not options.get('watch'):
            self.stdout.write(self.style.SUCCESS(f'processed={count}'))
