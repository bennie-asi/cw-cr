import time

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from django.db.models import Q
from django.utils import timezone

from review.models import ProjectGitLabSync
from review.services.gitlab_sync import synchronize_project


class Command(BaseCommand):
    help = '同步已启用项目的 GitLab MR，使用 --watch 持续执行。'

    def add_arguments(self, parser):
        parser.add_argument('--watch', action='store_true')
        parser.add_argument('--interval', type=float, default=2.0)
        parser.add_argument('--limit', type=int, default=5)

    def handle(self, *args, **options):
        if options['interval'] <= 0 or options['limit'] <= 0:
            raise CommandError('interval 和 limit 必须为正数')
        try:
            while True:
                close_old_connections()
                configs = ProjectGitLabSync.objects.filter(enabled=True, project__enabled=True).filter(
                    Q(next_sync_at__isnull=True) | Q(next_sync_at__lte=timezone.now()),
                ).order_by('next_sync_at', 'project_id')[:options['limit']]
                for config in configs:
                    try:
                        result = synchronize_project(config)
                        if not result.get('skipped'):
                            self.stdout.write(f'project={config.project_id} enqueued={result["enqueued"]}')
                    except Exception:
                        # Never print exception text: an upstream request may contain credentials.
                        self.stderr.write(f'project={config.project_id} 同步中断，下一轮重试')
                if not options['watch']:
                    return
                time.sleep(options['interval'])
        except KeyboardInterrupt:
            self.stdout.write('sync stopped')
