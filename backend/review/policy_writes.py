"""Separate ordinary project settings from guarded merge-gate writes."""

from collections.abc import Mapping

from rest_framework import serializers

from review.engines.registry import is_engine_selectable
from review.models import ProjectPolicy


class StrictPolicyWriteSerializer(serializers.ModelSerializer):
    def to_internal_value(self, data):
        if isinstance(data, Mapping):
            unknown = set(data) - set(self.fields)
            if unknown:
                raise serializers.ValidationError({
                    field: '此接口不允许修改该字段' for field in sorted(unknown)
                })
        return super().to_internal_value(data)


class ProjectPolicyWriteSerializer(StrictPolicyWriteSerializer):
    class Meta:
        model = ProjectPolicy
        fields = (
            'webhook_trigger', 'command_review', 'command_improve',
            'security_checks', 'reliability_checks', 'style_checks',
            'block_on_p1', 'block_on_p2', 'summary_prompt', 'engine',
            'security_prompt', 'reliability_prompt', 'style_prompt',
        )
        extra_kwargs = {field: {'max_length': 20000} for field in (
            'security_prompt', 'reliability_prompt', 'style_prompt',
        )}

    def to_internal_value(self, data):
        if isinstance(data, Mapping):
            errors = {}
            for field in ('security_prompt', 'reliability_prompt', 'style_prompt'):
                if field not in data:
                    continue
                value = data[field]
                if not isinstance(value, str):
                    errors[field] = '提示词必须是文本；空字符串表示继承平台'
                elif value and not value.strip():
                    errors[field] = '自定义提示词不能只有空白；空字符串表示继承平台'
            if errors:
                raise serializers.ValidationError(errors)
        return super().to_internal_value(data)

    def validate(self, attrs):
        p1 = attrs.get('block_on_p1', self.instance.block_on_p1 if self.instance else True)
        p2 = attrs.get('block_on_p2', self.instance.block_on_p2 if self.instance else False)
        if p2 and not p1:
            raise serializers.ValidationError({'block_on_p2': '严格门槛须同时包含 P1 问题'})
        return attrs

    def update(self, instance, validated_data):
        gate_fields = {
            'security_checks', 'reliability_checks', 'style_checks', 'block_on_p1', 'block_on_p2',
            'security_prompt', 'reliability_prompt', 'style_prompt', 'summary_prompt', 'engine',
        }
        if any(field in gate_fields and getattr(instance, field) != value
               for field, value in validated_data.items()):
            validated_data.update(dry_run_passed=False,
                                  dry_run_completed_at=None, dry_run_assessment={},
                                  dry_run_reason='项目要求已变更；后续完整评审将按新要求自动检查，旧检查预览已失效')
        return super().update(instance, validated_data)

    def validate_engine(self, value):
        project = self.instance.project if self.instance else None
        if not is_engine_selectable(value, project=project):
            raise serializers.ValidationError('此引擎尚未安装或未通过当前模型配置验证')
        return value


class MergeGateWriteSerializer(StrictPolicyWriteSerializer):
    class Meta:
        model = ProjectPolicy
        fields = ('auto_merge_enabled',)
