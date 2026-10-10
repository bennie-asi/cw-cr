"""Resolve repository ownership without rewriting historical path snapshots."""
from django.core.exceptions import ValidationError
from django.db import router

from review.models import Project, ProjectRepository, Review, ReviewIssue


def validate_repository_owner(instance, using=None):
    if not instance.repository_id or not instance.project_id:
        return
    alias = using or instance._state.db or router.db_for_write(type(instance), instance=instance)
    owner = ProjectRepository.objects.using(alias).filter(pk=instance.repository_id).values_list('project_id', flat=True).first()
    if owner is not None and owner != instance.project_id:
        raise ValidationError({'repository': 'Repository must belong to the review project.'})


def resolve_repository(project, path, *, historical=False):
    alias = project._state.db or router.db_for_write(ProjectRepository, instance=project)
    path = (path or project.gitlab_project_path).strip('/').removesuffix('.git')
    rows = ProjectRepository.objects.using(alias)
    repository = rows.filter(path=path).first()
    if repository:
        if repository.project_id == project.pk:
            return repository
        if historical:
            return None
        raise ValidationError({'repository': 'Repository must belong to the review project.'})
    is_main = path == project.gitlab_project_path.strip('/').removesuffix('.git')
    conflicting = (Project.objects.using(alias).filter(gitlab_project_path=path).exclude(pk=project.pk).exists()
                   or Review.objects.using(alias).filter(repository_path=path).exclude(project_id=project.pk).exists()
                   or ReviewIssue.objects.using(alias).filter(repository_path=path).exclude(project_id=project.pk).exists())
    if not path or conflicting or (not is_main and not historical):
        if historical:
            return None
        raise ValidationError({'repository': 'Repository ownership could not be resolved for this project.'})
    repository, _ = rows.get_or_create(path=path, defaults={
        'project': project, 'enabled': not historical,
        'gitlab_project_id': project.gitlab_project_id if is_main else '',
        'gitlab_url': project.gitlab_url if is_main else '',
    })
    if repository.project_id != project.pk:
        if historical:
            return None
        raise ValidationError({'repository': 'Repository must belong to the review project.'})
    return repository


def ensure_review_repository(review):
    if review.repository_id:
        return review.repository
    repository = resolve_repository(review.project, review.repository_path, historical=True)
    if repository:
        review.repository = repository
        review.save(update_fields=('repository',))
    return repository
