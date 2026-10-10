from django.test import TestCase
from rest_framework.test import APIClient


class HealthEndpointTests(TestCase):
    def test_health_endpoint_returns_ok(self):
        response = APIClient().get('/api/health/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ok')
