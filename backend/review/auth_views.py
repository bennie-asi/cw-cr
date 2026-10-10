"""Built-in account sessions. Business object authorization lives separately."""

from datetime import timedelta
from math import ceil

from django.contrib.auth import authenticate, get_user_model, login, logout, update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db.models import F
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from review.models import LoginAttemptBucket


def csrf_failure(request, reason=''):
    return JsonResponse({'detail': '安全校验失败，请刷新页面后重试'}, status=403)


def user_payload(user):
    return {
        'id': user.pk,
        'username': user.get_username(),
        'display_name': user.get_full_name() or user.get_username(),
        'platform_role': 'admin' if user.is_staff or user.is_superuser else 'member',
    }


def consume_bucket(domain, value, limit):
    """Fixed five-minute window shared by all server workers via the database."""
    now = timezone.now()
    key = salted_hmac('cw-cr-login-limit', f'{domain}:{value}', algorithm='sha256').hexdigest()
    bucket, _ = LoginAttemptBucket.objects.get_or_create(
        key=key, defaults={'expires_at': now + timedelta(minutes=5)},
    )
    # Conditional update prevents concurrent requests from each resetting an
    # expired bucket. F avoids lost increments across server processes.
    LoginAttemptBucket.objects.filter(pk=bucket.pk, expires_at__lte=now).update(
        attempts=0, expires_at=now + timedelta(minutes=5),
    )
    LoginAttemptBucket.objects.filter(pk=bucket.pk).update(attempts=F('attempts') + 1)
    bucket.refresh_from_db()
    if bucket.attempts > limit:
        return max(1, ceil((bucket.expires_at - now).total_seconds()))
    return 0


def consume_attempts(request, username):
    # Client-controlled forwarded headers are deliberately not trusted. Reverse
    # proxies must supply a trustworthy REMOTE_ADDR or share the IP quota.
    LoginAttemptBucket.objects.filter(expires_at__lt=timezone.now() - timedelta(days=1)).delete()
    return consume_bucket('ip', request.META.get('REMOTE_ADDR', ''), 60) or consume_bucket(
        'account', username.casefold(), 10,
    )


def read_credentials(data, first, second, first_limit=4096):
    if not isinstance(data, dict):
        return None
    left, right = data.get(first), data.get(second)
    if not isinstance(left, str) or not isinstance(right, str):
        return None
    if not left or not right or len(left) > first_limit or len(right) > 4096:
        return None
    return left, right


@never_cache
@api_view(['GET'])
@permission_classes([AllowAny])
def session_status(request):
    return Response({
        'user': user_payload(request.user) if request.user.is_authenticated else None,
        'csrf_token': get_token(request),
        'needs_setup': not get_user_model().objects.exists(),
    })


@never_cache
@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@csrf_protect
def sign_in(request):
    credentials = read_credentials(request.data, 'username', 'password', 150)
    if credentials is None:
        return Response({'detail': '请输入有效的用户名和密码'}, status=400)
    username, password = credentials
    retry_after = consume_attempts(request, username)
    if retry_after:
        return Response({'detail': '登录尝试过于频繁，请稍后重试'}, status=429, headers={'Retry-After': str(retry_after)})
    user = authenticate(request, username=username, password=password)
    if user is None:
        return Response({'detail': '账号或密码错误，或账号已停用'}, status=401)
    login(request._request, user)
    return Response({'user': user_payload(user), 'csrf_token': get_token(request)})


@never_cache
@api_view(['POST'])
@permission_classes([AllowAny])
@csrf_protect
def sign_out(request):
    logout(request._request)
    return Response({'user': None, 'csrf_token': get_token(request)})


@never_cache
@api_view(['POST'])
@permission_classes([AllowAny])
@csrf_protect
def change_password(request):
    if not request.user.is_authenticated:
        return Response({'detail': '请先登录'}, status=401)
    credentials = read_credentials(request.data, 'current_password', 'new_password')
    if credentials is None:
        return Response({'detail': '请输入有效的当前密码和新密码'}, status=400)
    current, replacement = credentials
    if not request.user.check_password(current):
        return Response({'detail': '当前密码不正确'}, status=400)
    try:
        validate_password(replacement, request.user)
    except ValidationError as exc:
        return Response({'detail': '；'.join(exc.messages)}, status=400)
    request.user.set_password(replacement)
    request.user.save(update_fields=['password'])
    update_session_auth_hash(request._request, request.user)
    return Response({'detail': '密码已更新', 'csrf_token': get_token(request)})
