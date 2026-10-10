from datetime import timedelta
from base64 import b64encode
import os
import runpy
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient


class SessionAuthTests(TestCase):
    password = 'Only-for-tests-4892!'

    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.user = get_user_model().objects.create_user('tester', password=self.password)

    def csrf(self, client=None):
        client = client or self.client
        response = client.get('/api/auth/session/')
        self.assertEqual(response.status_code, 200)
        return response.json()['csrf_token']

    def login(self, client=None, username='tester', password=None, **headers):
        client = client or self.client
        token = self.csrf(client)
        return client.post('/api/auth/login/', {
            'username': username, 'password': password or self.password,
        }, format='json', HTTP_X_CSRFTOKEN=token, **headers)

    def test_session_reports_setup_and_safe_identity(self):
        response = self.client.get('/api/auth/session/')
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['user'])
        self.assertFalse(response.json()['needs_setup'])
        self.user.delete()
        self.assertTrue(self.client.get('/api/auth/session/').json()['needs_setup'])

    def test_login_real_password_rotates_session_and_returns_safe_user(self):
        self.client.force_login(self.user)
        old_session = self.client.cookies[settings.SESSION_COOKIE_NAME].value
        self.user.is_staff = True
        self.user.save()
        before = self.csrf()
        response = self.login()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['user']['username'], 'tester')
        self.assertEqual(response.json()['user']['platform_role'], 'admin')
        self.assertNotIn('password', response.json()['user'])
        self.assertNotEqual(before, response.json()['csrf_token'])
        self.assertEqual(self.client.get('/api/auth/session/').json()['user']['id'], self.user.pk)
        # Already authenticated with same identity may reuse its session; a new
        # anonymous login must always rotate its pre-auth session key.
        self.assertTrue(old_session)
        fresh = APIClient(enforce_csrf_checks=True)
        session = fresh.session
        session['test_pre_auth'] = True
        session.save()
        fresh.cookies[settings.SESSION_COOKIE_NAME] = session.session_key
        old_key = session.session_key
        self.assertEqual(self.login(fresh).status_code, 200)
        self.assertNotEqual(old_key, fresh.cookies[settings.SESSION_COOKIE_NAME].value)
        self.assertEqual(self.client.cookies[settings.SESSION_COOKIE_NAME]['httponly'], True)

    def test_member_identity_has_no_admin_role(self):
        self.assertEqual(self.login().json()['user']['platform_role'], 'member')

    def test_basic_auth_cannot_bypass_session_login_limits(self):
        credential = b64encode(f'tester:{self.password}'.encode()).decode()
        response = self.client.get('/api/auth/session/', HTTP_AUTHORIZATION=f'Basic {credential}')
        self.assertIsNone(response.json()['user'])

    def test_admin_login_cannot_bypass_account_limits(self):
        self.assertEqual(self.client.get('/admin/login/').status_code, 404)

    def test_production_configuration_secures_cookies(self):
        with patch.dict(os.environ, {'DJANGO_DEBUG': 'false'}):
            config = runpy.run_path(str(settings.BASE_DIR / 'config' / 'settings.py'))
        self.assertTrue(config['SESSION_COOKIE_SECURE'])
        self.assertTrue(config['CSRF_COOKIE_SECURE'])
        self.assertTrue(config['SESSION_COOKIE_HTTPONLY'])
        self.assertEqual(config['CSRF_TRUSTED_ORIGINS'], config['CORS_ALLOWED_ORIGINS'])

    def test_password_policy_minimum_and_complexity(self):
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError
        for value in ['Ac7!mZ9?q', '123456789012345', 'password']:
            with self.subTest(password_case=len(value)), self.assertRaises(ValidationError):
                validate_password(value, self.user)
        validate_password('Ac7!mZ9?qR', self.user)
        similar_user = get_user_model()(username='developeraccount')
        with self.assertRaises(ValidationError) as error:
            validate_password('developeraccount', similar_user)
        self.assertIn('password_too_similar', [item.code for item in error.exception.error_list])

    def test_shared_credentials_accept_exact_upper_boundaries(self):
        from review.auth_views import read_credentials
        for limit in [150, 4096]:
            left, right = 'u' * limit, 'p' * 4096
            self.assertEqual(read_credentials({'left': left, 'right': right}, 'left', 'right', limit), (left, right))
            self.assertIsNone(read_credentials({'left': left + 'u', 'right': right}, 'left', 'right', limit))
            self.assertIsNone(read_credentials({'left': left, 'right': ''}, 'left', 'right', limit))

    def test_superuser_and_display_name_are_real_account_fields(self):
        self.user.is_superuser = True
        self.user.first_name = 'Example'
        self.user.last_name = 'Reviewer'
        self.user.save()
        result = self.login().json()['user']
        self.assertEqual(result['platform_role'], 'admin')
        self.assertEqual(result['display_name'], 'Example Reviewer')

    def test_old_csrf_token_is_invalid_after_login(self):
        previous_token = self.csrf()
        response = self.login()
        current_token = response.json()['csrf_token']
        self.assertEqual(self.client.post('/api/auth/logout/', HTTP_X_CSRFTOKEN=previous_token).status_code, 403)
        self.assertEqual(self.client.post('/api/auth/logout/', HTTP_X_CSRFTOKEN=current_token).status_code, 200)

    def test_invalid_unknown_and_disabled_credentials_are_indistinguishable(self):
        wrong = self.login(password='wrong')
        unknown = self.login(username='nobody')
        self.user.is_active = False
        self.user.save()
        disabled = self.login()
        for response in [wrong, unknown, disabled]:
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json(), {'detail': '账号或密码错误，或账号已停用'})
        self.assertIsNone(self.client.get('/api/auth/session/').json()['user'])

    def test_login_rejects_malformed_credentials(self):
        token = self.csrf()
        for payload in [[], {}, {'username': [], 'password': 'x'},
                        {'username': 'tester', 'password': 123},
                        {'username': '', 'password': 'x'},
                        {'username': 'tester', 'password': ''},
                        {'username': 'x' * 151, 'password': 'x'},
                        {'username': 'tester', 'password': 'x' * 4097}]:
            with self.subTest(payload_type=type(payload).__name__):
                response = self.client.post('/api/auth/login/', payload, format='json', HTTP_X_CSRFTOKEN=token)
                self.assertEqual(response.status_code, 400)

    def test_login_requires_csrf_and_rejects_untrusted_origin(self):
        response = self.client.post('/api/auth/login/', {'username': 'tester', 'password': self.password}, format='json')
        self.assertEqual(response.status_code, 403)
        response = self.login(HTTP_ORIGIN='https://evil.invalid')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.login(HTTP_ORIGIN='http://127.0.0.1:5176').status_code, 200)

    def test_account_limit_survives_client_and_ip_change(self):
        for _ in range(10):
            self.assertEqual(self.login(password='wrong').status_code, 401)
        other = APIClient(enforce_csrf_checks=True)
        response = self.login(other, REMOTE_ADDR='192.0.2.42')
        self.assertEqual(response.status_code, 429)
        self.assertGreater(int(response['Retry-After']), 0)
        self.assertIsNone(other.get('/api/auth/session/').json()['user'])

    def test_ip_limit_ignores_forged_forwarded_address(self):
        for i in range(60):
            response = self.login(username=f'unknown-{i}', HTTP_X_FORWARDED_FOR=f'192.0.2.{i}')
            self.assertEqual(response.status_code, 401)
        self.assertEqual(self.login().status_code, 429)

    def test_expired_limit_resets_and_storage_has_no_raw_credentials(self):
        from review.models import LoginAttemptBucket
        for _ in range(10):
            self.login(password='wrong')
        self.assertEqual(self.login().status_code, 429)
        LoginAttemptBucket.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.login().status_code, 200)
        for bucket in LoginAttemptBucket.objects.all():
            self.assertEqual(len(bucket.key), 64)
            self.assertNotIn('tester', bucket.key)
            self.assertNotIn('127.0.0.1', bucket.key)
            self.assertEqual(bucket.attempts, 1)

    def test_logout_requires_csrf_and_invalidates_session(self):
        self.assertEqual(self.login().status_code, 200)
        old_cookie = self.client.cookies[settings.SESSION_COOKIE_NAME].value
        self.assertEqual(self.client.post('/api/auth/logout/').status_code, 403)
        response = self.client.post('/api/auth/logout/', HTTP_X_CSRFTOKEN=self.csrf())
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.client.get('/api/auth/session/').json()['user'])
        replay = APIClient(enforce_csrf_checks=True)
        replay.cookies[settings.SESSION_COOKIE_NAME] = old_cookie
        self.assertIsNone(replay.get('/api/auth/session/').json()['user'])

    def test_password_change_requires_authentication(self):
        response = self.client.post('/api/auth/password/', {}, format='json', HTTP_X_CSRFTOKEN=self.csrf())
        self.assertEqual(response.status_code, 401)

    def test_password_change_rejects_wrong_old_weak_new_and_malformed(self):
        self.login()
        for payload in [{'current_password': 'wrong', 'new_password': 'Next-pass-5829!'},
                        {'current_password': self.password, 'new_password': '123'},
                        {'current_password': self.password, 'new_password': []},
                        {'current_password': '', 'new_password': 'Next-pass-5829!'},
                        {'current_password': self.password, 'new_password': ''},
                        {'current_password': 'x' * 4097, 'new_password': 'Next-pass-5829!'},
                        {'current_password': self.password, 'new_password': 'x' * 4097}, []]:
            response = self.client.post('/api/auth/password/', payload, format='json', HTTP_X_CSRFTOKEN=self.csrf())
            self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.password))

    def test_password_change_preserves_current_and_invalidates_other_sessions(self):
        other = APIClient(enforce_csrf_checks=True)
        self.login(other)
        self.login()
        response = self.client.post('/api/auth/password/', {
            'current_password': self.password, 'new_password': 'Changed-only-test-9127!',
        }, format='json', HTTP_X_CSRFTOKEN=self.csrf())
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('Changed-only-test-9127!'))
        self.assertIsNotNone(self.client.get('/api/auth/session/').json()['user'])
        self.assertIsNone(other.get('/api/auth/session/').json()['user'])

    def test_auth_responses_disable_caching_and_reject_wrong_methods(self):
        response = self.client.get('/api/auth/session/')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(self.client.get('/api/auth/login/').status_code, 405)
        self.assertEqual(self.client.put('/api/auth/session/').status_code, 405)
        self.assertTrue(settings.SESSION_COOKIE_HTTPONLY)
        self.assertEqual(settings.SESSION_COOKIE_SAMESITE, 'Lax')

    def test_all_auth_success_and_rejection_responses_disable_caching(self):
        responses = [self.login(password='wrong'), self.login()]
        responses.append(self.client.post('/api/auth/password/', {
            'current_password': 'wrong', 'new_password': 'Changed-only-test-9127!',
        }, format='json', HTTP_X_CSRFTOKEN=self.csrf()))
        responses.append(self.client.post('/api/auth/password/', {
            'current_password': self.password, 'new_password': 'Changed-only-test-9127!',
        }, format='json', HTTP_X_CSRFTOKEN=self.csrf()))
        responses.append(self.client.post('/api/auth/logout/'))
        responses.append(self.client.post('/api/auth/logout/', HTTP_X_CSRFTOKEN=self.csrf()))
        self.assertEqual([response.status_code for response in responses], [401, 200, 400, 200, 403, 200])
        for response in responses:
            self.assertIn('no-store', response['Cache-Control'])
