from django.test import TestCase
from unittest.mock import patch

from review.models import Project, ProjectPolicy
from review.policy_writes import MergeGateWriteSerializer, ProjectPolicyWriteSerializer


class PolicyWriteTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Policy', gitlab_project_path='test/policy')
        self.policy = ProjectPolicy.objects.create(project=self.project)

    def test_normal_fields_save_without_changing_gate(self):
        payload = {
            'webhook_trigger': False, 'command_review': False, 'command_improve': False,
            'security_checks': False, 'reliability_checks': False, 'style_checks': True,
            'block_on_p1': False, 'summary_prompt': '使用中文', 'engine': 'builtin',
        }
        serializer = ProjectPolicyWriteSerializer(self.policy, data=payload, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        self.policy.refresh_from_db()
        for field, value in payload.items():
            self.assertEqual(getattr(self.policy, field), value)
        self.assertFalse(self.policy.auto_merge_enabled)
        self.assertTrue(self.policy.dry_run_required)
        self.assertFalse(self.policy.dry_run_passed)

    def test_normal_endpoint_cannot_write_gate_identity_or_unknown_fields(self):
        for field, value in {
            'auto_merge_enabled': True, 'dry_run_required': False, 'dry_run_passed': True,
            'dry_run_completed_at': '2026-09-23T00:00:00Z', 'dry_run_reason': 'forged',
            'project': str(self.project.id), 'id': self.policy.id, 'unknown': True,
        }.items():
            with self.subTest(field=field):
                serializer = ProjectPolicyWriteSerializer(
                    self.policy, data={field: value, 'command_review': False}, partial=True,
                )
                self.assertFalse(serializer.is_valid())
                self.assertIn(field, serializer.errors)
                self.policy.refresh_from_db()
                self.assertTrue(self.policy.command_review)
                self.assertFalse(self.policy.auto_merge_enabled)

    @patch('review.policy_writes.is_engine_selectable', return_value=True)
    def test_project_policy_accepts_supported_engine_after_runtime_verification(self, _selectable):
        for engine in ('pr-agent', 'open-code-review', 'pi-agent'):
            with self.subTest(engine=engine):
                serializer = ProjectPolicyWriteSerializer(
                    self.policy, data={'engine': engine}, partial=True,
                )
                self.assertTrue(serializer.is_valid(), serializer.errors)
                serializer.save()
                self.policy.refresh_from_db()
                self.assertEqual(self.policy.engine, engine)

    def test_gate_accepts_only_normalized_toggle(self):
        for value, expected in ((True, True), (False, False), ('true', True), ('false', False), (1, True)):
            with self.subTest(value=value):
                serializer = MergeGateWriteSerializer(self.policy, data={'auto_merge_enabled': value}, partial=True)
                self.assertTrue(serializer.is_valid(), serializer.errors)
                self.assertIs(serializer.validated_data['auto_merge_enabled'], expected)
                serializer.save()
                self.policy.refresh_from_db()
                self.assertIs(self.policy.auto_merge_enabled, expected)

    def test_gate_rejects_normal_policy_and_server_owned_fields(self):
        for field in (
            'webhook_trigger', 'command_review', 'command_improve', 'security_checks',
            'reliability_checks', 'style_checks', 'block_on_p1', 'summary_prompt', 'engine',
            'dry_run_required', 'dry_run_passed', 'dry_run_completed_at', 'dry_run_reason',
            'project', 'id', 'unknown',
        ):
            with self.subTest(field=field):
                serializer = MergeGateWriteSerializer(
                    self.policy, data={field: True, 'auto_merge_enabled': True}, partial=True,
                )
                self.assertFalse(serializer.is_valid())
                self.assertIn(field, serializer.errors)
                self.policy.refresh_from_db()
                self.assertFalse(self.policy.auto_merge_enabled)

    def test_non_objects_and_bad_boolean_return_validation_errors(self):
        for serializer_type in (ProjectPolicyWriteSerializer, MergeGateWriteSerializer):
            for value in ([], ['auto_merge_enabled'], True, 'true', None):
                with self.subTest(serializer=serializer_type.__name__, value=value):
                    serializer = serializer_type(self.policy, data=value, partial=True)
                    self.assertFalse(serializer.is_valid())
                    self.assertIn('non_field_errors', serializer.errors)
        for value in (None, 'definitely', [], {}):
            with self.subTest(value=value):
                serializer = MergeGateWriteSerializer(self.policy, data={'auto_merge_enabled': value}, partial=True)
                self.assertFalse(serializer.is_valid())
                self.assertIn('auto_merge_enabled', serializer.errors)

    def test_empty_patch_preserves_fields(self):
        before = ProjectPolicy.objects.values().get(pk=self.policy.pk)
        for serializer_type in (ProjectPolicyWriteSerializer, MergeGateWriteSerializer):
            serializer = serializer_type(self.policy, data={}, partial=True)
            self.assertTrue(serializer.is_valid(), serializer.errors)
            self.assertEqual(serializer.validated_data, {})
        self.assertEqual(ProjectPolicy.objects.values().get(pk=self.policy.pk), before)
