from pathlib import Path
import os
import runpy
import tempfile
from unittest.mock import patch

from django.test import SimpleTestCase

from config.settings import BASE_DIR, database_config_from_url


class DatabaseUrlTests(SimpleTestCase):
    def test_sqlite_four_slash_url_preserves_absolute_volume_path(self):
        config = database_config_from_url('sqlite:////data/db.sqlite3')
        self.assertEqual(Path(config['NAME']), Path('/data/db.sqlite3'))

    def test_sqlite_url_resolves_relative_database_inside_backend(self):
        config = database_config_from_url('sqlite:///db.sqlite3')

        self.assertEqual(config['ENGINE'], 'django.db.backends.sqlite3')
        self.assertEqual(Path(config['NAME']), BASE_DIR / 'db.sqlite3')

    def test_postgres_url_maps_credentials_host_port_and_sslmode(self):
        config = database_config_from_url('postgresql://review:secret@db.internal:5432/cwcr?sslmode=require')

        self.assertEqual(config['ENGINE'], 'django.db.backends.postgresql')
        self.assertEqual(config['NAME'], 'cwcr')
        self.assertEqual(config['USER'], 'review')
        self.assertEqual(config['PASSWORD'], 'secret')
        self.assertEqual(config['HOST'], 'db.internal')
        self.assertEqual(config['PORT'], 5432)
        self.assertEqual(config['OPTIONS']['sslmode'], 'require')


class DeploymentSettingsTests(SimpleTestCase):
    def load_settings(self, environment, command='runserver'):
        with patch.dict(os.environ, environment, clear=True), patch('sys.argv', ['manage.py', command]), patch('dotenv.load_dotenv') as dotenv:
            result = runpy.run_path(str(BASE_DIR / 'config' / 'settings.py'))
        return result, dotenv

    def test_secret_file_is_used_by_management_commands_and_server(self):
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / 'django.key'
            key.write_text('a-persistent-secret-' + 'x' * 64, encoding='utf-8')
            configured, _ = self.load_settings({'DJANGO_SECRET_KEY_FILE': str(key)})
        self.assertEqual(configured['SECRET_KEY'], 'a-persistent-secret-' + 'x' * 64)

    def test_explicit_environment_secret_takes_precedence_over_file(self):
        configured, _ = self.load_settings({'DJANGO_SECRET_KEY': 'explicit-key', 'DJANGO_SECRET_KEY_FILE': '/missing/key'})
        self.assertEqual(configured['SECRET_KEY'], 'explicit-key')

    def test_http_cookie_setting_does_not_require_debug(self):
        configured, _ = self.load_settings({'DJANGO_DEBUG': 'false', 'DJANGO_SECURE_COOKIES': 'false'})
        self.assertFalse(configured['DEBUG'])
        self.assertFalse(configured['SESSION_COOKIE_SECURE'])
        self.assertFalse(configured['CSRF_COOKIE_SECURE'])

    def test_production_cookies_remain_secure_by_default(self):
        configured, _ = self.load_settings({'DJANGO_DEBUG': 'false'})
        self.assertTrue(configured['SESSION_COOKIE_SECURE'])
        self.assertTrue(configured['CSRF_COOKIE_SECURE'])

    def test_csrf_sources_can_be_configured_without_enabling_cors(self):
        configured, _ = self.load_settings({'DJANGO_CORS_ALLOWED_ORIGINS': '', 'DJANGO_CSRF_TRUSTED_ORIGINS': 'https://review.example.com'})
        self.assertEqual(configured['CORS_ALLOWED_ORIGINS'], [])
        self.assertEqual(configured['CSRF_TRUSTED_ORIGINS'], ['https://review.example.com'])

    def test_test_runner_does_not_load_developer_dotenv(self):
        configured, dotenv = self.load_settings({}, command='test')
        dotenv.assert_not_called()
        self.assertTrue(configured['REVIEW_EXECUTE_INLINE'])
