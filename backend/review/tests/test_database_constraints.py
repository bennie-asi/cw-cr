from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase

from review.models import ModelConfig, NotificationConfig, Project


class ConfigurationConstraintTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='constraints', gitlab_project_path='tests/constraints')

    def test_duplicate_global_configuration_is_rejected_by_database(self):
        for model in (ModelConfig, NotificationConfig):
            with self.subTest(model=model.__name__):
                model.objects.create(scope='global', project=None)
                with self.assertRaises(IntegrityError), transaction.atomic():
                    model.objects.create(scope='global', project=None)

    def test_invalid_scope_and_project_combinations_are_rejected_by_database(self):
        for model in (ModelConfig, NotificationConfig):
            for scope, project in (('global', self.project), ('project', None), ('unknown', None)):
                with self.subTest(model=model.__name__, scope=scope, project=project):
                    with self.assertRaises(IntegrityError), transaction.atomic():
                        model.objects.create(scope=scope, project=project)

    def test_project_overrides_remain_independent_of_global_configuration(self):
        other = Project.objects.create(name='other', gitlab_project_path='tests/other-constraints')
        for model in (ModelConfig, NotificationConfig):
            with self.subTest(model=model.__name__):
                model.objects.create(scope='global')
                first = model.objects.create(scope='project', project=self.project)
                second = model.objects.create(scope='project', project=other)
                self.assertNotEqual(first.pk, second.pk)
                with self.assertRaises(IntegrityError), transaction.atomic():
                    model.objects.create(scope='project', project=self.project)


class ConfigurationConstraintMigrationTests(TransactionTestCase):
    migrate_from = [('review', '0024_mr_issue_lifecycle')]
    migrate_to = [('review', '0025_config_constraints')]

    def setUp(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        self.old_apps = executor.loader.project_state(self.migrate_from).apps

    def tearDown(self):
        # Conflicting histories deliberately prevent forward migration. Remove
        # only the synthetic fixtures before restoring the current schema.
        for name in ('ModelConfig', 'NotificationConfig'):
            self.old_apps.get_model('review', name).objects.all().delete()
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def migrate(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        return executor.loader.project_state(self.migrate_to).apps

    def test_clean_history_keeps_values_and_timestamps(self):
        project = self.old_apps.get_model('review', 'Project').objects.create(name='old', gitlab_project_path='tests/old')
        records = []
        for name, field, value in (('ModelConfig', 'api_key', 'historical-model-key'),
                                   ('NotificationConfig', 'webhook_url', 'https://example.org/historical-hook')):
            model = self.old_apps.get_model('review', name)
            for scope, owner in (('global', None), ('project', project)):
                record = model.objects.create(scope=scope, project=owner, **{field: value})
                records.append((name, record.pk, field, value, record.updated_at))
        apps = self.migrate()
        for name, pk, field, value, updated_at in records:
            record = apps.get_model('review', name).objects.get(pk=pk)
            self.assertEqual(getattr(record, field), value)
            self.assertEqual(record.updated_at, updated_at)

    def test_duplicate_globals_fail_without_deleting_or_overwriting_rows(self):
        for name, field in (('ModelConfig', 'api_key'), ('NotificationConfig', 'webhook_url')):
            with self.subTest(model=name):
                model = self.old_apps.get_model('review', name)
                first = model.objects.create(scope='global', **{field: 'first-history'})
                second = model.objects.create(scope='global', **{field: 'second-history'})
                with self.assertRaisesMessage(RuntimeError, 'configuration history'):
                    self.migrate()
                self.assertEqual(dict(model.objects.values_list('pk', field)), {first.pk: 'first-history', second.pk: 'second-history'})
                model.objects.all().delete()

    def test_invalid_history_fails_without_data_loss(self):
        project = self.old_apps.get_model('review', 'Project').objects.create(name='invalid', gitlab_project_path='tests/invalid')
        for name in ('ModelConfig', 'NotificationConfig'):
            for scope, owner in (('global', project), ('project', None), ('unknown', None)):
                with self.subTest(model=name, scope=scope):
                    model = self.old_apps.get_model('review', name)
                    record = model.objects.create(scope=scope, project=owner)
                    with self.assertRaisesMessage(RuntimeError, 'configuration history'):
                        self.migrate()
                    self.assertTrue(model.objects.filter(pk=record.pk, scope=scope, project=owner).exists())
                    model.objects.all().delete()
