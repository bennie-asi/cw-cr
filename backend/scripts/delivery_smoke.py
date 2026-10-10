"""Exercise real session/CSRF/authorization with a fresh local database.

No saved connection, account or project from the normal database is loaded.
All outbound HTTP is blocked. The isolated database stays in ignored .runtime.
"""
import json
import os
from pathlib import Path
import secrets
import sys
from unittest.mock import patch
from uuid import uuid4
from cryptography.fernet import Fernet

backend_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(backend_dir))
(backend_dir / '.runtime').mkdir(exist_ok=True)
os.environ.update({
    'DJANGO_SETTINGS_MODULE': 'config.settings',
    'DATABASE_URL': f'sqlite:///.runtime/delivery-smoke-{uuid4().hex}.sqlite3',
    'DJANGO_SECRET_KEY': secrets.token_urlsafe(48),
    'CREDENTIAL_ENCRYPTION_KEY': Fernet.generate_key().decode('ascii'),
    'DJANGO_DEBUG': 'true',
    'DJANGO_ALLOWED_HOSTS': 'testserver,localhost,127.0.0.1',
    'REVIEW_ALLOW_TEST_FALLBACK': 'false',
    'REVIEW_EXECUTE_INLINE': 'false',
    'GITLAB_URL': '', 'GITLAB_TOKEN': '', 'GITLAB_WEBHOOK_SECRET': '',
    'CCSWITCH_BASE_URL': '', 'CCSWITCH_API_KEY': '', 'CCSWITCH_MODEL': '',
    'WECOM_WEBHOOK_URL': '', 'PUBLIC_BASE_URL': '',
})

import django  # noqa: E402
django.setup()
from django.contrib.auth import get_user_model  # noqa: E402
from django.core.management import call_command  # noqa: E402
from django.test import Client  # noqa: E402


def check(response, expected, label):
    if response.status_code != expected:
        raise AssertionError(f'{label}: expected {expected}, got {response.status_code}')
    return response.json() if response.content else None


def write(client, token, path, data, expected=200):
    return check(client.post(path, json.dumps(data), content_type='application/json', HTTP_X_CSRFTOKEN=token), expected, path)


def login(username, password):
    client = Client(enforce_csrf_checks=True)
    session = check(client.get('/api/auth/session/'), 200, 'anonymous session')
    result = write(client, session['csrf_token'], '/api/auth/login/', {'username': username, 'password': password})
    return client, result['csrf_token']


with patch('requests.sessions.Session.request', side_effect=AssertionError('Outbound HTTP is disabled in delivery smoke')):
    call_command('migrate', verbosity=0)
    admin_password, member_password = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    get_user_model().objects.create_superuser(username='smoke-admin', password=admin_password)
    anonymous = Client(enforce_csrf_checks=True)
    check(anonymous.get('/api/projects/'), 401, 'anonymous project access')
    admin, csrf = login('smoke-admin', admin_password)
    check(admin.post('/api/projects/', '{}', content_type='application/json'), 403, 'write without CSRF')
    project = write(admin, csrf, '/api/projects/', {'name': 'Delivery smoke', 'repositories': [{'path': 'smoke/delivery'}]}, 201)
    project_id = project['id']
    write(admin, csrf, '/api/users/', {'username': 'smoke-member', 'display_name': 'Smoke member', 'password': member_password, 'platform_role': 'member'}, 201)
    member, member_csrf = login('smoke-member', member_password)
    assert check(member.get('/api/projects/'), 200, 'member collection')['results'] == []
    check(member.get(f'/api/projects/{project_id}/'), 404, 'ungranted project hidden')
    write(admin, csrf, f'/api/projects/{project_id}/members/', {'username': 'smoke-member', 'role': 'member'}, 201)
    check(member.get(f'/api/projects/{project_id}/'), 200, 'granted project read')
    check(member.patch(f'/api/projects/{project_id}/policy/', '{}', content_type='application/json', HTTP_X_CSRFTOKEN=member_csrf), 403, 'member policy write forbidden')
    check(member.get('/api/users/'), 403, 'member platform users forbidden')
    write(member, member_csrf, '/api/projects/connection-check/', {'repositories': [{'path': 'smoke/delivery'}]}, 403)
    preflight = write(admin, csrf, '/api/projects/connection-check/', {'repositories': [{'path': 'smoke/delivery'}]})
    assert preflight['repositories'][0]['connection']['state'] == 'unconfigured'
    assert len(check(admin.get('/api/projects/'), 200, 'project persisted')['results']) == 1
    check(member.get('/api/overview/'), 200, 'member overview')
    coverage = check(member.get('/api/overview/coverage/'), 200, 'isolated coverage')
    assert coverage['percentage'] is None
    assert check(member.get('/api/reviews/'), 200, 'member history')['results'] == []
    write(member, member_csrf, '/api/auth/logout/', {})
    check(member.get('/api/projects/'), 401, 'logout invalidates access')

print('PASS: isolated DB; login, CSRF, project creation, separate member grant, permission isolation, read-only preflight, overview/coverage/history, logout; outbound HTTP blocked.')
