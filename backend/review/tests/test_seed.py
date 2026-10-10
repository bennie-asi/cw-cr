from django.core.management import call_command
from django.test import TestCase, override_settings

from review.models import ModelConfig, NotificationConfig, Project, ProjectPolicy, ReviewEngineConfig


class SeedDemoDataTests(TestCase):
    def test_seed_command_is_idempotent(self):
        call_command('seed_demo_data', verbosity=0)
        call_command('seed_demo_data', verbosity=0)

        self.assertEqual(Project.objects.filter(gitlab_project_path='example-team/demo-api').count(), 1)
        self.assertEqual(ProjectPolicy.objects.count(), 1)
        self.assertEqual(ModelConfig.objects.filter(scope=ModelConfig.Scope.GLOBAL).count(), 1)
        self.assertEqual(NotificationConfig.objects.filter(scope=NotificationConfig.Scope.GLOBAL).count(), 1)
        self.assertEqual(ReviewEngineConfig.objects.get(pk=1).default_engine, 'builtin')

    @override_settings(GITLAB_WEBHOOK_SECRET='')
    def test_seed_leaves_webhook_secret_for_page_configuration(self):
        call_command('seed_demo_data', verbosity=0)

        self.assertEqual(Project.objects.get(gitlab_project_path='example-team/demo-api').webhook_secret, '')
