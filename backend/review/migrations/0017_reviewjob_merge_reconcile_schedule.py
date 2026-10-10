from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('review', '0016_reviewjob_auto_merge_state')]

    operations = [
        migrations.AddField(
            model_name='reviewjob', name='merge_next_check_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='reviewjob', name='merge_reconcile_attempts',
            field=models.PositiveIntegerField(default=0),
        ),
    ]
