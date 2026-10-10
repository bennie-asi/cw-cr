from unittest.mock import patch

from django.core.management import call_command, get_commands
from django.test import TestCase
from django.utils import timezone

from review.tests.test_gitlab_sync import GitLabPollingTests, mr


class GitLabSyncCommandTests(TestCase):
    def setUp(self):
        GitLabPollingTests.setUp(self)

    def test_only_due_sync_is_processed(self):
        self.assertIn('sync_gitlab_mrs', get_commands())
        with patch('review.integrations.gitlab.GitLabClient.list_merge_requests', return_value=([mr(2)], False)) as read:
            call_command('sync_gitlab_mrs')
            read.assert_not_called()
            self.config.next_sync_at = timezone.now()
            self.config.save()
            call_command('sync_gitlab_mrs')
        self.config.refresh_from_db()
        self.assertEqual(self.config.sync_state, 'success')
        self.assertEqual(self.config.last_result['enqueued'], 1)

    def test_paused_project_is_not_scanned(self):
        self.assertIn('sync_gitlab_mrs', get_commands())
        self.config.next_sync_at = timezone.now()
        self.config.save()
        self.project.enabled = False
        self.project.save()
        with patch('review.integrations.gitlab.GitLabClient.list_merge_requests') as read:
            call_command('sync_gitlab_mrs')
        read.assert_not_called()
