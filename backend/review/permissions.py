"""Fail-closed platform/project authorization, before any integration side effect."""

from uuid import UUID

from django.shortcuts import get_object_or_404
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import ParseError
from rest_framework.permissions import BasePermission, SAFE_METHODS

from review.models import Project, ProjectMembership, Review


class PlatformSessionAuthentication(SessionAuthentication):
    def authenticate_header(self, request):
        # DRF otherwise translates unauthenticated Session requests into 403,
        # making session expiry indistinguishable from insufficient permission.
        return 'Session'


def is_platform_admin(user):
    return bool(user.is_authenticated and user.is_active and (user.is_staff or user.is_superuser))


def visible_projects(user):
    if not user.is_authenticated or not user.is_active:
        return Project.objects.none()
    if is_platform_admin(user):
        return Project.objects.all()
    return Project.objects.filter(memberships__user=user)


def requested_project(request):
    value = request.query_params.get('project')
    try:
        project_id = UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise ParseError('请显式选择有效的项目')
    return get_object_or_404(visible_projects(request.user), pk=project_id)


GLOBAL_ROUTES = {
    'prompt-config',
    'users', 'user-detail', 'user-reset-password',
    'system-status', 'system-retry', 'audit-logs',
    'model-config', 'model-config-test', 'engine-config', 'engine-config-test', 'notification-config', 'notification-config-test',
    'gitlab-config', 'gitlab-config-test', 'project-connection-check',
}
COLLECTION_ROUTES = {'projects', 'reviews', 'overview', 'overview-coverage'}
PROJECT_ROUTES = {
    'project-members', 'project-member-detail',
    'project-audit-logs',
    'project-detail', 'project-policy', 'project-engine-config', 'project-model-config', 'project-model-config-test',
    'project-notification-config', 'project-notification-config-test', 'project-notification-preview',
    'project-reviews', 'project-merge-requests', 'trigger-review',
}
REVIEW_ROUTES = {
    'review-detail', 'review-summary', 'review-notify', 'retry-review',
    'merge-review', 'review-merge-notify', 'publish-comment', 'resolve-comment',
}
GATE_ROUTES = {'merge-gate', 'merge-gate-dry-run'}
MEMBER_WRITES = {'project-reviews', 'trigger-review', 'review-summary', 'review-notify', 'review-merge-notify', 'retry-review', 'publish-comment', 'resolve-comment'}


class WorkspacePermission(BasePermission):
    message = '没有此操作的权限'

    def has_permission(self, request, view):
        user = request.user
        if not user.is_authenticated or not user.is_active:
            return False
        name = getattr(request.resolver_match, 'url_name', None)
        if name not in GLOBAL_ROUTES | COLLECTION_ROUTES | PROJECT_ROUTES | REVIEW_ROUTES | GATE_ROUTES:
            return False
        if name in GLOBAL_ROUTES:
            return is_platform_admin(user)
        if name in COLLECTION_ROUTES:
            if name == 'reviews' and request.query_params.get('project'):
                requested_project(request)
            return request.method in SAFE_METHODS or is_platform_admin(user)
        if name in GATE_ROUTES:
            project = requested_project(request)
        elif name in PROJECT_ROUTES:
            project = get_object_or_404(visible_projects(user), pk=view.kwargs['project_id'])
        else:
            review = get_object_or_404(Review.objects.filter(project__in=visible_projects(user)), pk=view.kwargs['review_id'])
            project = review.project
        if request.method in SAFE_METHODS or name in MEMBER_WRITES or is_platform_admin(user):
            return True
        return ProjectMembership.objects.filter(project=project, user=user, role=ProjectMembership.Role.ADMIN).exists()
