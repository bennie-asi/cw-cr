from rest_framework.decorators import api_view
from rest_framework.response import Response

from review.models import AuditLog
from review.serializers import AuditLogSerializer

NOTIFICATION_ACTIONS = (
    '发送企业微信评审通知',
    '企业微信通知失败',
    '测试企业微信通知',
    '企业微信投递结果不确定',
    '自动合并结果',
    '重发自动合并结果通知',
)


def filter_audit_category(queryset, request):
    if request.query_params.get('category') == 'notification':
        return queryset.filter(action__in=NOTIFICATION_ACTIONS)
    return queryset


@api_view(['GET'])
def project_audit_logs(request, project_id):
    # WorkspacePermission resolves the URL project through visible membership first.
    logs = filter_audit_category(AuditLog.objects.filter(project_id=project_id), request).order_by('-created_at', '-id')[:100]
    return Response(AuditLogSerializer(logs, many=True).data)
