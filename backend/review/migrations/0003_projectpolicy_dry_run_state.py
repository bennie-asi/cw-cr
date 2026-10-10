from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('review', '0002_review_additions_review_changed_files_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='projectpolicy',
            name='dry_run_completed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='projectpolicy',
            name='dry_run_passed',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='projectpolicy',
            name='dry_run_reason',
            field=models.TextField(blank=True, default=''),
        ),
    ]
