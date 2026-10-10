"""Verify the real Nginx/API stack with synthetic data in a temporary volume."""
from http.cookiejar import CookieJar
import json
import sys
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

BASE = 'http://localhost:8080'
client = build_opener(HTTPCookieProcessor(CookieJar()))


def request(path, expected=200, data=None, csrf=None, origin=None, method=None):
    headers = {'Content-Type': 'application/json'}
    if csrf:
        headers['X-CSRFToken'] = csrf
    if origin:
        headers['Origin'] = origin
    body = json.dumps(data).encode() if data is not None else None
    try:
        response = client.open(Request(BASE + path, body, headers, method=method), timeout=15)
    except HTTPError as error:
        response = error
    payload = response.read()
    assert response.code == expected, f'{path}: expected {expected}, got {response.code}'
    return json.loads(payload) if 'application/json' in response.headers.get('Content-Type', '') else payload


phase = sys.argv[1]
assert request('/api/health/')['status'] == 'ok'
assert b'<div id="root">' in request('/')
assert b'<div id="root">' in request('/projects/smoke/settings/policy')
request('/assets/missing.js', expected=404)
request('/ui-implementation-preview.html', expected=404)
request('/api/this-endpoint-does-not-exist/', expected=404)
request('/api/projects/', expected=401)
csrf = request('/api/auth/session/')['csrf_token']
login = {'username': 'container-smoke', 'password': 'cwcr-smoke-synthetic-password-2026!'}
request('/api/auth/login/', expected=403, data=login)
request('/api/auth/login/', expected=403, data=login, csrf=csrf, origin='https://untrusted.example')
csrf = request('/api/auth/login/', data=login, csrf=csrf)['csrf_token']
if phase == 'create':
    project = request('/api/projects/', expected=201, data={
        'name': 'Container smoke project', 'gitlab_project_path': 'example-team/demo-api',
        'repositories': [{'path': 'example-team/demo-api'}],
    }, csrf=csrf)
    assert project['name'] == 'Container smoke project'
    sync = request(f'/api/projects/{project["id"]}/sync/', data={'enabled': False, 'interval_seconds': 120}, csrf=csrf, method='PATCH')
    assert not sync['enabled'] and sync['interval_seconds'] == 120
projects = request('/api/projects/')['results']
assert len(projects) == 1 and projects[0]['name'] == 'Container smoke project'
sync = request(f'/api/projects/{projects[0]["id"]}/sync/')
assert not sync['enabled'] and sync['interval_seconds'] == 120
request('/api/auth/logout/', data={}, csrf=csrf)
request('/api/projects/', expected=401)
print(f'PASS ({phase}): HTTP stack, SPA, API errors, session, CSRF, origin checks, project persistence, logout')
