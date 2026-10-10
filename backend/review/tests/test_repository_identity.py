from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.models.deletion import ProtectedError
from django.test import TestCase, TransactionTestCase
from unittest.mock import patch

from review.models import Project, ProjectRepository, Review, ReviewIssue, ReviewJob
from review.services import mr_issues
from review.services.review_runner import create_review_job, run_review_job


FINDING = {'file_path': 'app.py', 'new_line': 3, 'level': 'P1', 'title': 'Missing input',
           'body': 'Handle missing input.', 'anchor_code': 'value = params["value"]'}


class RepositoryIdentityTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='workspace', gitlab_project_path='tests/main')
        self.repository = ProjectRepository.objects.create(project=self.project, path='tests/secondary')

    def review(self, **values):
        return Review.objects.create(project=self.project, repository=self.repository,
                                     repository_path=self.repository.path, mr_iid=7, **values)

    def test_secondary_repository_job_has_repository_association(self):
        job = create_review_job(self.project, {'mr_iid': 7, 'repository_path': self.repository.path})
        self.assertEqual(getattr(job.review, 'repository_id', None), self.repository.pk)
        self.assertEqual(job.review.repository_path, 'tests/secondary')

    def test_legacy_main_repository_job_resolves_without_changing_project_configuration(self):
        job = create_review_job(self.project, {'mr_iid': 7})
        self.assertIsNotNone(getattr(job.review, 'repository_id', None))
        self.assertEqual(job.review.repository.project_id, self.project.pk)
        self.project.refresh_from_db()
        self.assertEqual(self.project.gitlab_project_path, 'tests/main')

    def test_legacy_job_without_review_links_its_failed_review_to_repository(self):
        job = ReviewJob.objects.create(project=self.project, idempotency_key='legacy-without-review',
                                       payload={'mr_iid': 7, 'repository_path': self.repository.path}, max_attempts=1)
        with patch('review.services.review_runner.GitLabClient.fetch_merge_request', side_effect=RuntimeError('synthetic outage')):
            review = run_review_job(job.pk)
        self.assertEqual(review.status, Review.Status.FAILED)
        self.assertEqual(review.repository_id, self.repository.pk)

    def test_linked_issue_occurrences_retain_identity_after_repository_rename(self):
        first = self.review()
        issue = mr_issues.prepare_findings(first, [FINDING], 'prompt')[0][1]
        issue.latest_review = first
        issue.save()
        fingerprint = issue.fingerprint
        self.repository.path = 'tests/renamed'
        self.repository.save(update_fields=('path',))
        second = self.review()
        repeated = mr_issues.prepare_findings(second, [FINDING], 'prompt')[0][1]
        first.refresh_from_db()
        issue.refresh_from_db()
        self.assertEqual(first.repository_path, 'tests/secondary')
        self.assertEqual(issue.repository_path, 'tests/secondary')
        self.assertEqual(issue.repository_id, self.repository.pk)
        self.assertEqual(repeated.pk, issue.pk)
        self.assertEqual(issue.fingerprint, fingerprint)
        self.assertEqual(set(issue.occurrences.values_list('review_id', flat=True)), {first.pk, second.pk})

    def test_disabled_repository_keeps_protected_review_and_issue_history(self):
        review = self.review()
        issue = mr_issues.prepare_findings(review, [FINDING], 'prompt')[0][1]
        self.repository.enabled = False
        self.repository.save(update_fields=('enabled',))
        review.refresh_from_db()
        issue.refresh_from_db()
        self.assertEqual(review.repository_id, self.repository.pk)
        self.assertEqual(issue.repository_id, self.repository.pk)
        with self.assertRaises(ProtectedError):
            self.repository.delete()

    def test_explicit_cross_project_references_are_rejected(self):
        other = Project.objects.create(name='other', gitlab_project_path='tests/other')
        foreign = ProjectRepository.objects.create(project=other, path='tests/foreign')
        with self.assertRaises(ValidationError):
            Review.objects.create(project=self.project, repository=foreign, repository_path=foreign.path, mr_iid=7)
        with self.assertRaises(ValidationError):
            ReviewIssue.objects.create(project=self.project, repository=foreign, repository_path=foreign.path,
                                       mr_iid=7, fingerprint='a' * 64, **{k: v for k, v in FINDING.items() if k != 'level'})
        with self.assertRaises(ValidationError):
            create_review_job(self.project, {'mr_iid': 7, 'repository_path': foreign.path})

    def test_recycled_path_does_not_join_distinct_linked_repository_identities(self):
        old_review = self.review()
        old_issue = mr_issues.prepare_findings(old_review, [FINDING], 'prompt')[0][1]
        self.repository.path = 'tests/renamed'
        self.repository.save(update_fields=('path',))
        recycled = ProjectRepository.objects.create(project=self.project, path='tests/secondary')
        new_review = Review.objects.create(project=self.project, repository=recycled, repository_path=recycled.path, mr_iid=7)
        new_issue = mr_issues.prepare_findings(new_review, [FINDING], 'prompt')[0][1]
        self.assertNotEqual(old_issue.pk, new_issue.pk)
        self.assertEqual(list(mr_issues.scope_reviews(new_review)), [new_review])

    def test_unlinked_legacy_history_is_included_by_snapshot_path(self):
        legacy = Review.objects.create(project=self.project, repository_path=self.repository.path, mr_iid=7)
        legacy.comments.create(**FINDING)
        linked = self.review()
        prepared = mr_issues.prepare_findings(linked, [FINDING], 'prompt')
        self.assertEqual(legacy.comments.get().issue_id, prepared[0][1].pk)
        self.assertEqual(prepared[0][1].repository_id, self.repository.pk)


class RepositoryIdentityMigrationTests(TransactionTestCase):
    migrate_from = [('review', '0025_config_constraints')]
    migrate_to = [('review', '0026_repository_identity')]

    def setUp(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        self.old_apps = executor.loader.project_state(self.migrate_from).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_backfill_links_owned_rows_and_creates_disabled_unambiguous_histories(self):
        Project = self.old_apps.get_model('review', 'Project')
        Repository = self.old_apps.get_model('review', 'ProjectRepository')
        Review = self.old_apps.get_model('review', 'Review')
        Issue = self.old_apps.get_model('review', 'ReviewIssue')
        project = Project.objects.create(name='old', gitlab_project_path='tests/main',
                                         gitlab_project_id='123', gitlab_url='https://gitlab.example/tests/main')
        existing = Repository.objects.create(project=project, path='tests/secondary', enabled=False)
        main = Review.objects.create(project=project, repository_path='', mr_iid=1)
        secondary = Review.objects.create(project=project, repository_path=existing.path, mr_iid=2)
        history = Review.objects.create(project=project, repository_path='tests/old-path', mr_iid=3)
        issue = Issue.objects.create(project=project, repository_path=history.repository_path, mr_iid=3,
                                     fingerprint='a' * 64, file_path='app.py', title='old', body='old')
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        apps = executor.loader.project_state(self.migrate_to).apps
        Review = apps.get_model('review', 'Review')
        Repository = apps.get_model('review', 'ProjectRepository')
        main_repo = Repository.objects.get(path='tests/main')
        self.assertFalse(main_repo.enabled)
        self.assertEqual(main_repo.gitlab_project_id, '123')
        self.assertEqual(main_repo.gitlab_url, project.gitlab_url)
        self.assertEqual(Review.objects.get(pk=main.pk).repository_id, main_repo.pk)
        self.assertEqual(Review.objects.get(pk=main.pk).repository_path, '')
        self.assertEqual(Review.objects.get(pk=secondary.pk).repository_id, existing.pk)
        historical_repo = Repository.objects.get(path=history.repository_path)
        self.assertFalse(historical_repo.enabled)
        self.assertEqual(apps.get_model('review', 'ReviewIssue').objects.get(pk=issue.pk).repository_id, historical_repo.pk)
        unchanged = apps.get_model('review', 'Project').objects.get(pk=project.pk)
        self.assertEqual(unchanged.gitlab_project_path, 'tests/main')

    def test_ambiguous_and_foreign_paths_are_left_unlinked(self):
        Project = self.old_apps.get_model('review', 'Project')
        Repository = self.old_apps.get_model('review', 'ProjectRepository')
        Review = self.old_apps.get_model('review', 'Review')
        first = Project.objects.create(name='first', gitlab_project_path='tests/first')
        second = Project.objects.create(name='second', gitlab_project_path='tests/second')
        Repository.objects.create(project=second, path='tests/foreign')
        records = [Review.objects.create(project=first, repository_path='tests/foreign', mr_iid=1),
                   Review.objects.create(project=first, repository_path='tests/shared-history', mr_iid=2),
                   Review.objects.create(project=second, repository_path='tests/shared-history', mr_iid=3),
                   Review.objects.create(project=first, repository_path=second.gitlab_project_path, mr_iid=4)]
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        apps = executor.loader.project_state(self.migrate_to).apps
        for record in records:
            self.assertIsNone(apps.get_model('review', 'Review').objects.get(pk=record.pk).repository_id)
        self.assertFalse(apps.get_model('review', 'ProjectRepository').objects.filter(path='tests/shared-history').exists())
