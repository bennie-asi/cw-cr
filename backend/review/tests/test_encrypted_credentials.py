from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings

from review.models import GitLabConfig, ModelConfig, NotificationConfig, Project
from review.serializers import GitLabConfigSerializer, ModelConfigSerializer, NotificationConfigSerializer, ProjectSerializer
from review.tests.auth_helpers import admin_client


PREFIX = 'credential:v1:'
TEST_KEY = Fernet.generate_key().decode('ascii')
FIELD_CASES = ((Project, 'webhook_secret'), (ModelConfig, 'api_key'),
               (GitLabConfig, 'api_token'), (GitLabConfig, 'webhook_secret'),
               (NotificationConfig, 'webhook_url'))


def raw_value(instance, field, value=None, *, write=False):
    quote = connection.ops.quote_name
    table, column = quote(instance._meta.db_table), quote(instance._meta.get_field(field).column)
    pk_column = quote(instance._meta.pk.column)
    pk = instance._meta.pk.get_db_prep_value(instance.pk, connection)
    with connection.cursor() as cursor:
        if write:
            cursor.execute(f'UPDATE {table} SET {column} = %s WHERE {pk_column} = %s', [value, pk])
            return
        cursor.execute(f'SELECT {column} FROM {table} WHERE {pk_column} = %s', [pk])
        return cursor.fetchone()[0]


@override_settings(CREDENTIAL_ENCRYPTION_KEY=TEST_KEY)
class EncryptedCredentialTests(TestCase):
    def records(self):
        return [
            (Project.objects.create(name='encrypted', gitlab_project_path='tests/encrypted', webhook_secret='project-private-secret'), 'webhook_secret', 'project-private-secret'),
            (ModelConfig.objects.create(api_key='model-private-secret'), 'api_key', 'model-private-secret'),
            (GitLabConfig.objects.create(api_token='gitlab-private-token', webhook_secret='gitlab-private-hook'), 'api_token', 'gitlab-private-token'),
            (GitLabConfig.objects.get(pk=1), 'webhook_secret', 'gitlab-private-hook'),
            (NotificationConfig.objects.create(webhook_url='https://example.org/hook?key=private'), 'webhook_url', 'https://example.org/hook?key=private'),
        ]

    def assert_encrypted(self, obj, field, plaintext):
        stored = raw_value(obj, field)
        self.assertNotEqual(stored, plaintext)
        self.assertTrue(stored.startswith(PREFIX))
        self.assertNotIn(plaintext, stored)
        obj.refresh_from_db()
        self.assertEqual(getattr(obj, field), plaintext)
        self.assertEqual(type(obj).objects.values_list(field, flat=True).get(pk=obj.pk), plaintext)

    def test_all_five_credentials_are_encrypted_in_raw_database_storage(self):
        for obj, field, plaintext in self.records():
            with self.subTest(model=type(obj).__name__, field=field):
                self.assert_encrypted(obj, field, plaintext)

    def test_queryset_update_and_bulk_update_encrypt_each_credential(self):
        for obj, field, _ in self.records():
            with self.subTest(model=type(obj).__name__, field=field):
                plaintext = 'https://example.org/updated-hook' if field == 'webhook_url' else 'updated-private-value'
                type(obj).objects.filter(pk=obj.pk).update(**{field: plaintext})
                self.assert_encrypted(obj, field, plaintext)
                plaintext = 'https://example.org/bulk-hook' if field == 'webhook_url' else 'bulk-private-value'
                setattr(obj, field, plaintext)
                type(obj).objects.bulk_update([obj], [field])
                self.assert_encrypted(obj, field, plaintext)

    def test_bulk_create_encrypts_all_five_credentials(self):
        projects = Project.objects.bulk_create([
            Project(name='bulk', gitlab_project_path='tests/bulk', webhook_secret='bulk-project-secret'),
        ])
        configs = [ModelConfig(scope='project', project=projects[0], api_key='bulk-model-secret')]
        ModelConfig.objects.bulk_create(configs)
        gitlab = GitLabConfig(id=2, api_token='bulk-gitlab-token', webhook_secret='bulk-gitlab-hook')
        GitLabConfig.objects.bulk_create([gitlab])
        notification = NotificationConfig(scope='project', project=projects[0], webhook_url='https://example.org/bulk-secret')
        NotificationConfig.objects.bulk_create([notification])
        for obj, field, value in ((projects[0], 'webhook_secret', 'bulk-project-secret'), (configs[0], 'api_key', 'bulk-model-secret'),
                                  (gitlab, 'api_token', 'bulk-gitlab-token'), (gitlab, 'webhook_secret', 'bulk-gitlab-hook'),
                                  (notification, 'webhook_url', 'https://example.org/bulk-secret')):
            self.assert_encrypted(obj, field, value)

    def test_plaintext_maximum_lengths_round_trip_with_text_storage(self):
        project = Project.objects.create(name='length', gitlab_project_path='tests/length')
        for model, field in FIELD_CASES:
            with self.subTest(model=model.__name__, field=field):
                column = model._meta.get_field(field)
                plaintext = ('https://example.org/' + 'x' * 180) if field == 'webhook_url' else '密' * 255
                self.assertEqual(len(plaintext), column.max_length)
                if model is Project:
                    obj = project
                elif model is GitLabConfig:
                    obj, _ = model.objects.get_or_create(pk=1)
                else:
                    obj, _ = model.objects.get_or_create(scope='project', project=project)
                setattr(obj, field, plaintext)
                obj.save()
                self.assert_encrypted(obj, field, plaintext)
                column.clean(plaintext, obj)
                with self.assertRaises(ValidationError):
                    column.clean(plaintext + 'x', obj)
                # Exercise PostgreSQL's type mapping without importing its
                # optional driver or connecting to a database server.
                postgres = SimpleNamespace(vendor='postgresql', data_types={'TextField': 'text', 'CharField': 'varchar(%(max_length)s)'},
                                           ops=SimpleNamespace(quote_name=lambda value: value))
                self.assertEqual(column.db_type(postgres), 'text')

    def test_legacy_plaintext_can_be_read_then_is_encrypted_when_written(self):
        obj = ModelConfig.objects.create()
        raw_value(obj, 'api_key', 'legacy-key', write=True)
        obj.refresh_from_db()
        self.assertEqual(obj.api_key, 'legacy-key')
        obj.save()
        self.assert_encrypted(obj, 'api_key', 'legacy-key')

    def test_blank_values_do_not_require_a_key_or_change_sql_storage(self):
        with TemporaryDirectory() as directory, override_settings(CREDENTIAL_ENCRYPTION_KEY='', CREDENTIAL_ENCRYPTION_KEY_FILE=Path(directory) / 'absent.key'):
            for model, field in FIELD_CASES:
                defaults = {'name': 'blank', 'gitlab_project_path': 'tests/blank'} if model is Project else {}
                if model is GitLabConfig:
                    obj, _ = model.objects.get_or_create(pk=1)
                else:
                    obj, _ = model.objects.get_or_create(**defaults)
                self.assertEqual(raw_value(obj, field), '')
                self.assertEqual(getattr(obj, field), '')
            self.assertFalse((Path(directory) / 'absent.key').exists())

    def test_missing_key_fails_writes_without_generating_a_replacement(self):
        obj = ModelConfig.objects.create()
        with TemporaryDirectory() as directory, override_settings(CREDENTIAL_ENCRYPTION_KEY='', CREDENTIAL_ENCRYPTION_KEY_FILE=Path(directory) / 'absent.key'):
            with self.assertRaises(ImproperlyConfigured), transaction.atomic():
                ModelConfig.objects.filter(pk=obj.pk).update(api_key='do-not-store-plaintext')
            self.assertFalse((Path(directory) / 'absent.key').exists())
            self.assertEqual(raw_value(obj, 'api_key'), '')

    def test_invalid_key_and_wrong_key_fail_with_safe_errors(self):
        obj = ModelConfig.objects.create(api_key='never-display-this-secret')
        for key in ('invalid-key', Fernet.generate_key().decode('ascii')):
            with self.subTest(invalid=key == 'invalid-key'), override_settings(CREDENTIAL_ENCRYPTION_KEY=key):
                with self.assertRaises(ImproperlyConfigured) as raised:
                    obj.refresh_from_db()
                self.assertNotIn('never-display-this-secret', str(raised.exception))
                self.assertNotIn(key, str(raised.exception))

    def test_tampered_and_unknown_version_ciphertexts_are_rejected(self):
        obj = ModelConfig.objects.create(api_key='protected')
        stored = raw_value(obj, 'api_key')
        for broken in (PREFIX + 'damaged-payload', stored[:-12] + 'x' * 12, 'credential:v9:unsupported'):
            with self.subTest(kind=broken[:14]):
                raw_value(obj, 'api_key', broken, write=True)
                with self.assertRaises(ImproperlyConfigured) as raised:
                    obj.refresh_from_db()
                self.assertNotIn(broken, str(raised.exception))

    def test_environment_key_takes_precedence_over_key_file(self):
        obj = ModelConfig.objects.create(api_key='env-key-protected')
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'credential.key'
            path.write_bytes(Fernet.generate_key())
            with override_settings(CREDENTIAL_ENCRYPTION_KEY=TEST_KEY, CREDENTIAL_ENCRYPTION_KEY_FILE=path):
                obj.refresh_from_db()
                self.assertEqual(obj.api_key, 'env-key-protected')

    def test_file_key_reads_and_invalid_file_fails_safely(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'credential.key'
            path.write_text(TEST_KEY, encoding='ascii')
            with override_settings(CREDENTIAL_ENCRYPTION_KEY='', CREDENTIAL_ENCRYPTION_KEY_FILE=path):
                obj = ModelConfig.objects.create(api_key='file-key-protected')
                self.assert_encrypted(obj, 'api_key', 'file-key-protected')
                path.write_text('bad-file-key', encoding='ascii')
                with self.assertRaises(ImproperlyConfigured) as raised:
                    obj.refresh_from_db()
                self.assertNotIn('bad-file-key', str(raised.exception))

    def test_migration_deconstruction_does_not_contain_a_key(self):
        for model, field in FIELD_CASES:
            self.assertNotIn(TEST_KEY, repr(model._meta.get_field(field).deconstruct()))

    def test_api_read_masks_secrets_and_blank_updates_preserve_saved_credentials(self):
        client = admin_client()
        records = self.records()
        for obj, field, plaintext in records:
            endpoint = f'/api/projects/{obj.pk}/' if isinstance(obj, Project) else {
                ModelConfig: '/api/model-config/', GitLabConfig: '/api/gitlab-config/', NotificationConfig: '/api/notification-config/',
            }[type(obj)]
            with self.subTest(model=type(obj).__name__, field=field):
                response = client.get(endpoint)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(plaintext, str(response.data))
                self.assertNotIn(field, response.data)
                response = client.patch(endpoint, {field: ''}, format='json')
                self.assertEqual(response.status_code, 200, response.data)
                obj.refresh_from_db()
                self.assertEqual(getattr(obj, field), plaintext)

    def test_api_plaintext_length_and_url_validation_are_preserved(self):
        for serializer, field, limit in ((ModelConfigSerializer, 'api_key', 255), (GitLabConfigSerializer, 'api_token', 255),
                                         (GitLabConfigSerializer, 'webhook_secret', 255), (ProjectSerializer, 'webhook_secret', 255),
                                         (NotificationConfigSerializer, 'webhook_url', 200)):
            value = 'https://example.org/' + 'x' * (limit - len('https://example.org/')) if field == 'webhook_url' else 'x' * limit
            accepted = serializer(data={field: value, 'name': 'validation', 'gitlab_project_path': 'tests/validation'}, partial=True)
            self.assertTrue(accepted.is_valid(), accepted.errors)
            rejected = serializer(data={field: value + 'x', 'name': 'validation', 'gitlab_project_path': 'tests/validation'}, partial=True)
            self.assertFalse(rejected.is_valid())
            self.assertIn(field, rejected.errors)
        for value in ('broken-url', 'ftp://example.org/hook'):
            self.assertFalse(NotificationConfigSerializer(data={'webhook_url': value}, partial=True).is_valid())


class CredentialKeyInitializationTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'secrets' / 'credential.key'
        self.override = override_settings(CREDENTIAL_ENCRYPTION_KEY='', CREDENTIAL_ENCRYPTION_KEY_FILE=self.path)
        self.override.enable()
        self.addCleanup(self.override.disable)

    def initialize(self):
        output = StringIO()
        call_command('initialize_credential_key', stdout=output)
        return output.getvalue()

    def test_explicit_initialization_command_is_registered(self):
        from django.core.management import get_commands
        self.assertIn('initialize_credential_key', get_commands())

    def test_explicit_initialization_creates_valid_key_without_printing_it(self):
        output = self.initialize()
        key = self.path.read_bytes().strip()
        Fernet(key)
        self.assertNotIn(key.decode('ascii'), output)
        self.assertIn(str(self.path), output)
        if __import__('os').name != 'nt':
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_existing_valid_key_is_not_replaced(self):
        self.path.parent.mkdir()
        original = Fernet.generate_key()
        self.path.write_bytes(original)
        self.initialize()
        self.assertEqual(self.path.read_bytes(), original)

    def test_invalid_existing_file_is_not_overwritten(self):
        self.path.parent.mkdir()
        self.path.write_bytes(b'invalid-existing-key')
        with self.assertRaises(CommandError):
            self.initialize()
        self.assertEqual(self.path.read_bytes(), b'invalid-existing-key')

    def test_configured_environment_key_avoids_file_creation(self):
        with override_settings(CREDENTIAL_ENCRYPTION_KEY=TEST_KEY):
            self.assertNotIn(TEST_KEY, self.initialize())
        self.assertFalse(self.path.exists())

    def test_invalid_environment_key_fails_without_file_creation(self):
        with override_settings(CREDENTIAL_ENCRYPTION_KEY='invalid-env-key'), self.assertRaises(CommandError):
            self.initialize()
        self.assertFalse(self.path.exists())

    def test_ciphertext_with_lost_file_refuses_new_key(self):
        with override_settings(CREDENTIAL_ENCRYPTION_KEY=TEST_KEY):
            ModelConfig.objects.create(api_key='recover-original-key')
        with self.assertRaisesMessage(CommandError, 'existing encrypted credentials'):
            self.initialize()
        self.assertFalse(self.path.exists())

    def test_unknown_version_ciphertext_also_refuses_new_key(self):
        obj = ModelConfig.objects.create()
        raw_value(obj, 'api_key', 'credential:v9:unknown', write=True)
        with self.assertRaisesMessage(CommandError, 'existing encrypted credentials'):
            self.initialize()
        self.assertFalse(self.path.exists())

    def test_exclusive_creation_does_not_replace_concurrently_created_key(self):
        import os
        original_open = os.open
        original_key = Fernet.generate_key()

        def create_before_open(path, flags, mode):
            self.path.write_bytes(original_key)
            return original_open(path, flags, mode)

        with patch('review.management.commands.initialize_credential_key.os.open', side_effect=create_before_open):
            self.initialize()
        self.assertEqual(self.path.read_bytes(), original_key)

    def test_missing_tables_are_not_queried(self):
        with patch.object(connection.introspection, 'table_names', return_value=[]), self.assertNumQueries(0):
            self.initialize()
        Fernet(self.path.read_bytes().strip())

    def test_permission_failure_removes_new_key_file_without_writing_key_material(self):
        import subprocess
        with patch('review.management.commands.initialize_credential_key.restrict_key_file_permissions',
                   side_effect=subprocess.CalledProcessError(1, ['icacls'])):
            with self.assertRaisesMessage(CommandError, 'securely initialize'):
                self.initialize()
        self.assertFalse(self.path.exists())

    def test_windows_permissions_use_argument_lists_for_current_user_and_system(self):
        import os
        if os.name != 'nt':
            self.skipTest('Windows DACL verification')
        from review.management.commands.initialize_credential_key import restrict_key_file_permissions
        sid = 'S-1-5-21-123-456-789-1001'
        with patch('review.management.commands.initialize_credential_key.subprocess.run') as run:
            run.return_value.stdout = f'"test-user","{sid}"'
            restrict_key_file_permissions(self.path, None)
        self.assertEqual(run.call_args_list[0].args[0], ['whoami', '/user', '/fo', 'csv', '/nh'])
        self.assertEqual(run.call_args_list[1].args[0],
                         ['icacls', str(self.path), '/inheritance:r', '/grant:r', f'*{sid}:(F)', '*S-1-5-18:(F)'])
        for call in run.call_args_list:
            self.assertFalse(call.kwargs.get('shell', False))


@override_settings(CREDENTIAL_ENCRYPTION_KEY=TEST_KEY)
class CredentialMigrationTests(TransactionTestCase):
    migrate_from = [('review', '0026_repository_identity')]
    migrate_to = [('review', '0027_encrypted_credentials')]

    def setUp(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        self.old_apps = executor.loader.project_state(self.migrate_from).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_forward_and_reverse_keep_original_values_and_timestamps(self):
        project = self.old_apps.get_model('review', 'Project').objects.create(name='history', gitlab_project_path='tests/history', webhook_secret='old-project-secret')
        model = self.old_apps.get_model('review', 'ModelConfig').objects.create(api_key='old-model-key')
        gitlab = self.old_apps.get_model('review', 'GitLabConfig').objects.create(api_token='old-gitlab-token', webhook_secret='old-gitlab-hook')
        notification = self.old_apps.get_model('review', 'NotificationConfig').objects.create(webhook_url='https://example.org/old-hook')
        blank = self.old_apps.get_model('review', 'ModelConfig').objects.create(scope='project', project=project)
        records = [(project, 'webhook_secret', 'old-project-secret'), (model, 'api_key', 'old-model-key'),
                   (gitlab, 'api_token', 'old-gitlab-token'), (gitlab, 'webhook_secret', 'old-gitlab-hook'),
                   (notification, 'webhook_url', 'https://example.org/old-hook')]
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        apps = executor.loader.project_state(self.migrate_to).apps
        for obj, field, value in records:
            self.assertTrue(raw_value(obj, field).startswith(PREFIX))
            migrated = apps.get_model('review', obj._meta.model_name).objects.get(pk=obj.pk)
            self.assertEqual(getattr(migrated, field), value)
            self.assertEqual(migrated.updated_at, obj.updated_at)
            self.assertEqual(migrated.created_at, obj.created_at)
        self.assertEqual(raw_value(blank, 'api_key'), '')
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        for obj, field, value in records:
            obj.refresh_from_db()
            self.assertEqual(raw_value(obj, field), value)
            self.assertEqual(getattr(obj, field), value)

    def test_missing_key_rejects_plaintext_migration_without_changing_history(self):
        obj = self.old_apps.get_model('review', 'ModelConfig').objects.create(api_key='unchanged-history')
        with TemporaryDirectory() as directory, override_settings(CREDENTIAL_ENCRYPTION_KEY='', CREDENTIAL_ENCRYPTION_KEY_FILE=Path(directory) / 'missing.key'):
            with self.assertRaises(ImproperlyConfigured):
                MigrationExecutor(connection).migrate(self.migrate_to)
        self.assertEqual(raw_value(obj, 'api_key'), 'unchanged-history')

    def test_wrong_key_prevents_reverse_migration_without_changing_ciphertext(self):
        obj = self.old_apps.get_model('review', 'ModelConfig').objects.create(api_key='rollback-protected')
        MigrationExecutor(connection).migrate(self.migrate_to)
        original = raw_value(obj, 'api_key')
        with override_settings(CREDENTIAL_ENCRYPTION_KEY=Fernet.generate_key().decode('ascii')):
            with self.assertRaises(ImproperlyConfigured):
                MigrationExecutor(connection).migrate(self.migrate_from)
        self.assertEqual(raw_value(obj, 'api_key'), original)


class TestRunnerKeyIsolationTests(SimpleTestCase):
    def settings_probe(self, command, *, key_source='environment'):
        import json
        import os
        import subprocess
        import sys
        from django.conf import settings

        probe = '''
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch
from cryptography.fernet import Fernet, InvalidToken

sys.path.insert(0, os.environ['CREDENTIAL_SETTINGS_PROBE_BACKEND'])
with patch('dotenv.load_dotenv'):
    import config.settings as config
from review.credential_crypto import configured_fernet
key = config.CREDENTIAL_ENCRYPTION_KEY
valid_key = False
if key:
    Fernet(key.encode('ascii'))
    valid_key = True
file_matches = False
if os.environ['CREDENTIAL_SETTINGS_PROBE_SOURCE'] == 'file':
    expected = Fernet(Path(os.environ['CREDENTIAL_ENCRYPTION_KEY_FILE']).read_bytes())
    try:
        file_matches = expected.decrypt(configured_fernet().encrypt(b'synthetic-settings-probe')) == b'synthetic-settings-probe'
    except InvalidToken:
        pass
print(json.dumps({
    'key_matches_environment': bool(key) and key == os.environ['CREDENTIAL_ENCRYPTION_KEY'],
    'uses_configured_file': not key and config.CREDENTIAL_ENCRYPTION_KEY_FILE == Path(os.environ['CREDENTIAL_ENCRYPTION_KEY_FILE']),
    'file_key_round_trip': file_matches,
    'key_valid': valid_key,
    'inline': config.REVIEW_EXECUTE_INLINE,
    'test_fallback': config.REVIEW_ALLOW_TEST_FALLBACK,
}))
'''
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'synthetic.key'
            path.write_bytes(Fernet.generate_key())
            environment = os.environ.copy()
            environment.update({
                'DJANGO_SETTINGS_MODULE': 'config.settings',
                'DATABASE_URL': 'sqlite:///.runtime/settings-probe-never-opened.sqlite3',
                'CREDENTIAL_SETTINGS_PROBE_BACKEND': str(settings.BASE_DIR),
                'CREDENTIAL_SETTINGS_PROBE_SOURCE': key_source,
                'CREDENTIAL_ENCRYPTION_KEY': TEST_KEY if key_source == 'environment' else '',
                'CREDENTIAL_ENCRYPTION_KEY_FILE': str(path),
                'REVIEW_ALLOW_TEST_FALLBACK': 'true',
            })
            environment.pop('REVIEW_EXECUTE_INLINE', None)
            result = subprocess.run([sys.executable, '-c', probe, *command], env=environment,
                                    check=True, capture_output=True, text=True)
            self.assertNotIn(TEST_KEY, result.stdout)
            return json.loads(result.stdout)

    def assert_ordinary_probe(self, option, *, key_source):
        result = self.settings_probe(['initialize_data', '--project-path', 'group/project', option, 'test'], key_source=key_source)
        self.assertEqual(result['key_matches_environment'], key_source == 'environment')
        self.assertEqual(result['uses_configured_file'], key_source == 'file')
        if key_source == 'file':
            self.assertTrue(result['file_key_round_trip'])
        self.assertFalse(result['inline'])
        self.assertFalse(result['test_fallback'])

    def test_django_test_runner_uses_an_ephemeral_key(self):
        from django.conf import settings
        self.assertTrue(settings.CREDENTIAL_ENCRYPTION_KEY)
        Fernet(settings.CREDENTIAL_ENCRYPTION_KEY.encode('ascii'))

    def test_runner_ignores_supplied_deployment_key_and_generates_an_independent_key(self):
        result = self.settings_probe(['test', 'review'])
        self.assertTrue(result['key_valid'])
        self.assertFalse(result['key_matches_environment'])
        self.assertFalse(result['uses_configured_file'])
        self.assertTrue(result['inline'])
        self.assertTrue(result['test_fallback'])

    def test_normal_commands_use_explicit_environment_key_and_file(self):
        result = self.settings_probe(['check'])
        self.assertTrue(result['key_matches_environment'])
        self.assertFalse(result['inline'])
        self.assertFalse(result['test_fallback'])

    def test_initialize_data_name_test_keeps_environment_key(self):
        self.assert_ordinary_probe('--name', key_source='environment')

    def test_initialize_data_owner_test_keeps_environment_key(self):
        self.assert_ordinary_probe('--owner', key_source='environment')

    def test_initialize_data_name_test_keeps_file_key(self):
        self.assert_ordinary_probe('--name', key_source='file')

    def test_initialize_data_owner_test_keeps_file_key(self):
        self.assert_ordinary_probe('--owner', key_source='file')

    def test_ordinary_command_label_test_cannot_enable_test_only_defaults(self):
        result = self.settings_probe(['initialize_data', '--project-path', 'group/project', '--name', 'test'])
        self.assertFalse(result['inline'])
        self.assertFalse(result['test_fallback'])
