from pathlib import Path
import os
import sys
from urllib.parse import parse_qs, unquote, urlparse

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
# The management command occupies argv[1]; option values such as --name test
# must never enable test credentials or synthetic integration behavior.
_running_tests = len(sys.argv) > 1 and sys.argv[1] == 'test'
if not _running_tests:
    load_dotenv(BASE_DIR / '.env')
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', '').strip()
if not SECRET_KEY:
    secret_file = os.getenv('DJANGO_SECRET_KEY_FILE', '').strip()
    if secret_file:
        from django.core.exceptions import ImproperlyConfigured
        try:
            SECRET_KEY = Path(secret_file).read_text(encoding='utf-8').strip()
        except OSError:
            raise ImproperlyConfigured('Django secret key file is unavailable; restore the original file.') from None
        if not SECRET_KEY:
            raise ImproperlyConfigured('Django secret key file is empty.')
    else:
        SECRET_KEY = 'local-development-only-change-me'
# Test credentials never use a developer or deployment key. Unit tests can
# override these settings explicitly to exercise key/file failures.
if _running_tests:
    from cryptography.fernet import Fernet
    CREDENTIAL_ENCRYPTION_KEY = Fernet.generate_key().decode('ascii')
else:
    CREDENTIAL_ENCRYPTION_KEY = os.getenv('CREDENTIAL_ENCRYPTION_KEY', '').strip()
CREDENTIAL_ENCRYPTION_KEY_FILE = Path(os.getenv('CREDENTIAL_ENCRYPTION_KEY_FILE', '').strip() or BASE_DIR / '.runtime' / 'secrets' / 'credential.key')
DEBUG = os.getenv('DJANGO_DEBUG', 'true').lower() == 'true'
ALLOWED_HOSTS = [host.strip() for host in os.getenv('DJANGO_ALLOWED_HOSTS', '*').split(',') if host.strip()]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'corsheaders',
    'rest_framework',
    'review',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'
TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [],
    'APP_DIRS': True,
    'OPTIONS': {
        'context_processors': [
            'django.template.context_processors.request',
            'django.contrib.auth.context_processors.auth',
            'django.contrib.messages.context_processors.messages',
        ],
    },
}]
WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'

def database_config_from_url(database_url: str) -> dict:
    parsed = urlparse(database_url)
    scheme = parsed.scheme.lower()
    if scheme in ('sqlite', 'sqlite3'):
        raw_path = unquote(parsed.path or '')
        if raw_path in ('', '/'):
            name = BASE_DIR / 'db.sqlite3'
        elif parsed.netloc:
            name = Path(f'//{parsed.netloc}{raw_path}')
        elif raw_path.startswith('//'):
            # SQLAlchemy-style four-slash URLs designate an absolute POSIX path.
            name = Path(raw_path[1:])
        elif raw_path.startswith('/'):
            name = BASE_DIR / raw_path.lstrip('/')
        else:
            name = BASE_DIR / raw_path
        return {'ENGINE': 'django.db.backends.sqlite3', 'NAME': name}
    if scheme in ('postgres', 'postgresql'):
        query = parse_qs(parsed.query)
        options = {key: values[-1] for key, values in query.items() if values}
        return {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': unquote(parsed.path.lstrip('/')),
            'USER': unquote(parsed.username or ''),
            'PASSWORD': unquote(parsed.password or ''),
            'HOST': parsed.hostname or '',
            'PORT': parsed.port or '',
            'OPTIONS': options,
        }
    raise ValueError(f'Unsupported DATABASE_URL scheme: {parsed.scheme or "missing"}')


DATABASES = {'default': database_config_from_url(os.getenv('DATABASE_URL', 'sqlite:///db.sqlite3'))}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 10}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SECURE = os.getenv('DJANGO_SECURE_COOKIES', str(not DEBUG)).lower() == 'true'
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
CSRF_FAILURE_VIEW = 'review.auth_views.csrf_failure'
LANGUAGE_CODE = 'zh-hans'
TIME_ZONE = 'Asia/Shanghai'
USE_I18N = True
USE_TZ = True
STATIC_URL = 'static/'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

CORS_ALLOWED_ORIGINS = [origin.strip() for origin in os.getenv('DJANGO_CORS_ALLOWED_ORIGINS', 'http://localhost:5173,http://127.0.0.1:5173,http://localhost:5174,http://127.0.0.1:5174,http://localhost:5176,http://127.0.0.1:5176').split(',') if origin.strip()]
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = [origin.strip() for origin in os.getenv('DJANGO_CSRF_TRUSTED_ORIGINS', ','.join(CORS_ALLOWED_ORIGINS)).split(',') if origin.strip()]
if os.getenv('DJANGO_TRUST_PROXY', 'false').lower() == 'true':
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': ['review.permissions.PlatformSessionAuthentication'],
    'DEFAULT_PERMISSION_CLASSES': ['review.permissions.WorkspacePermission'],
    'DEFAULT_RENDERER_CLASSES': ['rest_framework.renderers.JSONRenderer'],
}

REVIEW_EXECUTE_INLINE = os.getenv('REVIEW_EXECUTE_INLINE', 'true' if _running_tests else 'false').lower() == 'true'
REVIEW_MAX_RETRIES = int(os.getenv('REVIEW_MAX_RETRIES', '3'))
REVIEW_RETRY_BACKOFF_SECONDS = int(os.getenv('REVIEW_RETRY_BACKOFF_SECONDS', '5'))
# Synthetic integration responses are available only while the Django test
# runner is active.  A normal server process can never turn them on through an
# environment variable, so an unconfigured integration fails visibly instead
# of creating a fake review.
REVIEW_ALLOW_TEST_FALLBACK = os.getenv(
    'REVIEW_ALLOW_TEST_FALLBACK',
    'true' if _running_tests else 'false',
).lower() == 'true'
if not _running_tests:
    REVIEW_ALLOW_TEST_FALLBACK = False
GITLAB_REQUEST_TIMEOUT = float(os.getenv('GITLAB_REQUEST_TIMEOUT', '8'))
PUBLIC_BASE_URL = os.getenv('PUBLIC_BASE_URL', '').strip().rstrip('/')
LLM_REQUEST_TIMEOUT = float(os.getenv('LLM_REQUEST_TIMEOUT', '30'))
WECOM_REQUEST_TIMEOUT = float(os.getenv('WECOM_REQUEST_TIMEOUT', '8'))
REVIEW_MAX_CONCURRENCY = int(os.getenv('REVIEW_MAX_CONCURRENCY', '1'))
REVIEW_ENGINE_ARCHIVE_MAX_BYTES = int(os.getenv('REVIEW_ENGINE_ARCHIVE_MAX_BYTES', str(200 * 1024 * 1024)))
REVIEW_PR_AGENT_PYTHON = os.getenv('REVIEW_PR_AGENT_PYTHON', '').strip()
GITLAB_URL = os.getenv('GITLAB_URL', '').rstrip('/')
GITLAB_TOKEN = os.getenv('GITLAB_TOKEN', '')
GITLAB_WEBHOOK_SECRET = os.getenv('GITLAB_WEBHOOK_SECRET', '')
CCSWITCH_BASE_URL = os.getenv('CCSWITCH_BASE_URL', '').rstrip('/')
CCSWITCH_API_KEY = os.getenv('CCSWITCH_API_KEY', '')
CCSWITCH_MODEL = os.getenv('CCSWITCH_MODEL', '')
WECOM_WEBHOOK_URL = os.getenv('WECOM_WEBHOOK_URL', '')
