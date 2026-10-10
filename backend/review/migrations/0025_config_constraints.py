from django.db import migrations, models


def check_configuration_history(apps, schema_editor):
    alias = schema_editor.connection.alias
    for name in ('ModelConfig', 'NotificationConfig'):
        rows = apps.get_model('review', name).objects.using(alias)
        valid = models.Q(scope='global', project__isnull=True) | models.Q(scope='project', project__isnull=False)
        if rows.exclude(valid).exists() or rows.filter(scope='global').count() > 1:
            raise RuntimeError(
                f'{name} configuration history contains conflicting global rows or invalid scope/project combinations. '
                'Resolve the conflicting rows explicitly before migrating; no configuration values were changed.'
            )


class Migration(migrations.Migration):
    dependencies = [('review', '0024_mr_issue_lifecycle')]

    operations = [
        migrations.RunPython(check_configuration_history, migrations.RunPython.noop),
        migrations.RemoveConstraint(model_name='modelconfig', name='unique_model_scope_project'),
        migrations.RemoveConstraint(model_name='notificationconfig', name='unique_notification_scope_project'),
        migrations.AddConstraint(
            model_name='modelconfig',
            constraint=models.UniqueConstraint(fields=('scope',), condition=models.Q(scope='global'), name='unique_global_model_config'),
        ),
        migrations.AddConstraint(
            model_name='modelconfig',
            constraint=models.CheckConstraint(condition=(models.Q(scope='global', project__isnull=True) | models.Q(scope='project', project__isnull=False)), name='valid_model_config_scope'),
        ),
        migrations.AddConstraint(
            model_name='notificationconfig',
            constraint=models.UniqueConstraint(fields=('scope',), condition=models.Q(scope='global'), name='unique_global_notification'),
        ),
        migrations.AddConstraint(
            model_name='notificationconfig',
            constraint=models.CheckConstraint(condition=(models.Q(scope='global', project__isnull=True) | models.Q(scope='project', project__isnull=False)), name='valid_notification_scope'),
        ),
    ]
