from django.core.exceptions import FieldDoesNotExist
from django.test import TestCase

from review.models import ModelConfig, Project, ProjectPolicy, ReviewEngineConfig
from review.tests.auth_helpers import admin_client


class ReviewEngineConfigurationTests(TestCase):
    def setUp(self):
        self.client = admin_client()
        self.project = Project.objects.create(name='engine scope', gitlab_project_path='team/engine-scope')
        self.policy = ProjectPolicy.objects.create(project=self.project)

    def test_engine_selection_is_separate_from_model_connection(self):
        with self.assertRaises(FieldDoesNotExist):
            ModelConfig._meta.get_field('engine')
        self.assertTrue(self.project.engine_inherit)
        self.assertEqual(self.policy.engine, 'builtin')
        self.assertEqual(ReviewEngineConfig.objects.count(), 1)

    def test_platform_engine_endpoint_returns_explicit_effective_and_runtime_state(self):
        response = self.client.get('/api/engine-config/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['effective_engine'], 'builtin')
        self.assertIsInstance(response.data['engines'], list)
        self.assertIn('availability', response.data['engines'][0])

    def test_project_engine_inherits_global_and_restores_without_erasing_override(self):
        response = self.client.get(f'/api/projects/{self.project.pk}/engine-config/')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['inherited'])
        self.assertEqual(response.data['effective_engine'], 'builtin')

        response = self.client.patch(
            f'/api/projects/{self.project.pk}/engine-config/',
            {'inherited': True, 'engine': 'pi-agent'}, format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_project_cannot_run_engine_verification(self):
        response = self.client.post(f'/api/projects/{self.project.pk}/engine-config/test/', {'engine': 'pi-agent'}, format='json')
        self.assertEqual(response.status_code, 404)
