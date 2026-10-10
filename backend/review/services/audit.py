from review.models import AuditLog


def record_audit(*, action, target='', metadata=None, project=None, user=None):
    """Record explicit trusted ownership; never infer project from display text."""
    actor_user = user if user is not None and user.is_authenticated else None
    return AuditLog.objects.create(
        project=project,
        actor_user=actor_user,
        actor=actor_user.get_username() if actor_user is not None else 'system',
        action=action,
        target=target,
        metadata=metadata if metadata is not None else {},
    )
