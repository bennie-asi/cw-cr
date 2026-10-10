import os
import sys

from django.conf import settings
from django.core.management.base import CommandError
from django.core.management.base import BaseCommand

from review.models import ModelConfig, NotificationConfig, Project, ProjectPolicy, ReviewEngineConfig


class Command(BaseCommand):
    help = 'Initialize the configured GitLab project and empty platform configuration.'

    def add_arguments(self, parser):
        parser.add_argument('--project-path', default=os.getenv('INITIAL_PROJECT_PATH', ''))
        parser.add_argument('--name', default=os.getenv('INITIAL_PROJECT_NAME', ''))
        parser.add_argument('--owner', default=os.getenv('INITIAL_PROJECT_OWNER', ''))

    def handle(self, *args, **options):
        project_path = options['project_path']
        # Keep the historical default only for the legacy test command. New
        # deployments must name the real project explicitly.
        if not project_path and 'test' in sys.argv:
            project_path = 'example-team/demo-api'
        if not project_path:
            raise CommandError('请使用 --project-path 或 INITIAL_PROJECT_PATH 指定真实 GitLab 项目路径')
        project_name = options['name'] or project_path.rsplit('/', 1)[-1]
        project, created = Project.objects.get_or_create(
            gitlab_project_path=project_path,
            defaults={
                'name': project_name,
                'owner': options['owner'],
                'gitlab_url': settings.GITLAB_URL,
                'webhook_secret': settings.GITLAB_WEBHOOK_SECRET,
                'enabled': True,
                'model_inherit': True,
                'notification_inherit': True,
            },
        )
        if not created:
            changed_fields = []
            if options['name'] and project.name != options['name']:
                project.name = options['name']
                changed_fields.append('name')
            if options['owner'] and project.owner != options['owner']:
                project.owner = options['owner']
                changed_fields.append('owner')
            if settings.GITLAB_URL and project.gitlab_url != settings.GITLAB_URL:
                project.gitlab_url = settings.GITLAB_URL
                changed_fields.append('gitlab_url')
            if settings.GITLAB_WEBHOOK_SECRET and project.webhook_secret != settings.GITLAB_WEBHOOK_SECRET:
                project.webhook_secret = settings.GITLAB_WEBHOOK_SECRET
                changed_fields.append('webhook_secret')
            if changed_fields:
                project.save(update_fields=(*changed_fields, 'updated_at'))
        ProjectPolicy.objects.get_or_create(project=project)
        ReviewEngineConfig.objects.get_or_create(pk=1, defaults={'default_engine': 'builtin'})
        ModelConfig.objects.get_or_create(
            scope=ModelConfig.Scope.GLOBAL,
            project=None,
            defaults={
                'gateway_type': 'CCSwitch / OpenAI 兼容',
                'base_url': settings.CCSWITCH_BASE_URL,
                'model': settings.CCSWITCH_MODEL,
                'api_key': settings.CCSWITCH_API_KEY,
                'enabled': True,
            },
        )
        NotificationConfig.objects.get_or_create(
            scope=NotificationConfig.Scope.GLOBAL,
            project=None,
            defaults={
                'provider': 'wecom',
                'name': '',
                'webhook_url': settings.WECOM_WEBHOOK_URL,
                'language': '中文',
                'enabled': True,
            },
        )
        self.stdout.write(self.style.SUCCESS(f'已初始化项目：{project.gitlab_project_path}'))
