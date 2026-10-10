from collections.abc import Mapping

from rest_framework import serializers, status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from review.models import GlobalPromptConfig
from review.services.audit import record_audit


class GlobalPromptSerializer(serializers.ModelSerializer):
    class Meta:
        model = GlobalPromptConfig
        fields = ('general_prompt', 'review_prompt', 'improve_prompt', 'security_prompt', 'reliability_prompt', 'style_prompt')
        extra_kwargs = {field: {'max_length': 20000, 'allow_blank': False} for field in fields}

    def to_internal_value(self, data):
        if isinstance(data, Mapping):
            unknown = set(data) - set(self.fields)
            if unknown:
                raise serializers.ValidationError({field: '此接口不允许修改该字段' for field in sorted(unknown)})
        return super().to_internal_value(data)


@api_view(['GET', 'PATCH'])
def prompt_config(request):
    config, _ = GlobalPromptConfig.objects.get_or_create(pk=1)
    if request.method == 'GET':
        return Response(GlobalPromptSerializer(config).data)
    serializer = GlobalPromptSerializer(config, data=request.data, partial=True)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    serializer.save()
    record_audit(user=request.user, project=None, action='更新全局评审提示词', target='全局配置', metadata={'fields': list(request.data.keys())})
    return Response(serializer.data)
