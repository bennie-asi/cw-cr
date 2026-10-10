from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('review', '0015_projectpolicy_dry_run_assessment')]

    operations = [
        migrations.AddField(
            model_name='reviewjob', name='merge_status',
            field=models.CharField(default='not_requested', max_length=20),
        ),
        migrations.AddField(
            model_name='reviewjob', name='merge_attempts',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='reviewjob', name='merge_error',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='reviewjob', name='merge_result',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='reviewjob', name='merge_notification_state',
            field=models.CharField(default='not_requested', max_length=20),
        ),
    ]
