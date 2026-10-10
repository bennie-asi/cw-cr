"""Project-authorized synchronization configuration and background requests."""
from collections.abc import Mapping
from datetime import timedelta

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import api_view
from rest_framework.response import Response

from review.models import Project, ProjectGitLabSync
from review.services.audit import record_audit
from review.services.execution_locks import ResourceBusy, resource_lock
from review.services.gitlab_sync import SyncUnavailable, activate_sync, sync_status


class SyncSettingsSerializer(serializers.Serializer):
    enabled = serializers.BooleanField(required=False)
    interval_seconds = serializers.IntegerField(min_value=30, max_value=3600, required=False)

    def to_internal_value(self, data):
        if not isinstance(data, Mapping) or set(data) - set(self.fields):
            raise serializers.ValidationError({'detail': '仅允许提交 enabled 和 interval_seconds'})
        if 'enabled' in data and not isinstance(data['enabled'], bool):
            raise serializers.ValidationError({'enabled': '必须为布尔值'})
        return super().to_internal_value(data)


@api_view(['GET', 'PATCH'])
def project_sync(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    config, _ = ProjectGitLabSync.objects.get_or_create(project=project)
    if request.method == 'GET':
        return Response(sync_status(config))
    serializer = SyncSettingsSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    values = serializer.validated_data
    try:
        with resource_lock(f'gitlab-sync:{project.pk}'):
            config.refresh_from_db()
            interval = values.get('interval_seconds', config.interval_seconds)
            enabled = values.get('enabled', config.enabled)
            if enabled and not config.enabled:
                activate_sync(config, interval)
            else:
                config.enabled = enabled
                config.interval_seconds = interval
                config.next_sync_at = timezone.now() + timedelta(seconds=interval) if enabled else None
                config.save(update_fields=('enabled', 'interval_seconds', 'next_sync_at', 'updated_at'))
            record_audit(user=request.user, project=project, action='更新 GitLab 定时同步', target=project.name,
                         metadata={'enabled': config.enabled, 'interval_seconds': config.interval_seconds})
            return Response(sync_status(config))
    except SyncUnavailable as exc:
        return Response({'detail': str(exc)}, status=503)
    except ResourceBusy:
        return Response({'detail': '项目正在同步，请稍后保存'}, status=409)


@api_view(['POST'])
def request_project_sync(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    config, _ = ProjectGitLabSync.objects.get_or_create(project=project)
    try:
        with resource_lock(f'gitlab-sync:{project.pk}'):
            config.refresh_from_db()
            if not config.enabled or not project.enabled:
                return Response({'detail': '请先启用同步并恢复项目'}, status=409)
            config.next_sync_at = timezone.now()
            config.save(update_fields=('next_sync_at', 'updated_at'))
            record_audit(user=request.user, project=project, action='请求 GitLab 立即同步', target=project.name)
            return Response(sync_status(config), status=202)
    except ResourceBusy:
        return Response({'detail': '项目正在同步，请稍后刷新'}, status=409)
