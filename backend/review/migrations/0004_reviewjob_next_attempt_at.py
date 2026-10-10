from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('review', '0003_projectpolicy_dry_run_state'),
    ]

    operations = [
        migrations.AddField(
            model_name='reviewjob',
            name='next_attempt_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
