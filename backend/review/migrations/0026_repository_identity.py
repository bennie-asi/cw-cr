import django.db.models.deletion
from django.db import migrations, models


def backfill_repository_identity(apps, schema_editor):
    alias = schema_editor.connection.alias
    Project = apps.get_model('review', 'Project')
    Repository = apps.get_model('review', 'ProjectRepository')
    Review = apps.get_model('review', 'Review')
    Issue = apps.get_model('review', 'ReviewIssue')
    projects = {project.pk: project for project in Project.objects.using(alias).all()}
    claims = {}
    for project in projects.values():
        if project.gitlab_project_path:
            claims.setdefault(project.gitlab_project_path, set()).add(project.pk)
    histories = []
    for model in (Review, Issue):
        for pk, project_id, snapshot in model.objects.using(alias).values_list('pk', 'project_id', 'repository_path').iterator():
            path = snapshot or projects[project_id].gitlab_project_path
            if path:
                claims.setdefault(path, set()).add(project_id)
                histories.append((model, pk, project_id, path))
    repositories = {repository.path: repository for repository in Repository.objects.using(alias).all()}
    for model, pk, project_id, path in histories:
        repository = repositories.get(path)
        if repository is None and claims[path] == {project_id}:
            project = projects[project_id]
            is_main = path == project.gitlab_project_path
            repository = Repository.objects.using(alias).create(
                project_id=project_id, path=path, enabled=False,
                gitlab_project_id=project.gitlab_project_id if is_main else '',
                gitlab_url=project.gitlab_url if is_main else '',
            )
            repositories[path] = repository
        if repository is not None and repository.project_id == project_id:
            model.objects.using(alias).filter(pk=pk).update(repository_id=repository.pk)


class Migration(migrations.Migration):
    dependencies = [('review', '0025_config_constraints')]

    operations = [
        migrations.AddField(model_name='review', name='repository', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='reviews', to='review.projectrepository')),
        migrations.AddField(model_name='reviewissue', name='repository', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='issues', to='review.projectrepository')),
        migrations.RunPython(backfill_repository_identity, migrations.RunPython.noop),
        migrations.AddIndex(model_name='review', index=models.Index(fields=['project', 'repository', 'mr_iid', '-created_at'], name='review_repository_mr_idx')),
        migrations.AddIndex(model_name='reviewissue', index=models.Index(fields=['project', 'repository', 'mr_iid', 'status'], name='issue_repository_mr_idx')),
        migrations.RemoveConstraint(model_name='reviewissue', name='unique_mr_issue_fingerprint'),
        migrations.AddConstraint(model_name='reviewissue', constraint=models.UniqueConstraint(fields=('project', 'repository_path', 'mr_iid', 'fingerprint'), condition=models.Q(repository__isnull=True), name='unique_unlinked_mr_issue')),
        migrations.AddConstraint(model_name='reviewissue', constraint=models.UniqueConstraint(fields=('project', 'repository', 'mr_iid', 'fingerprint'), condition=models.Q(repository__isnull=False), name='unique_repository_mr_issue')),
    ]
