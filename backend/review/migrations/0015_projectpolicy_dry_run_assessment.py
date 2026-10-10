from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('review', '0014_audit_ownership')]

    operations = [
        migrations.AddField(
            model_name='projectpolicy', name='dry_run_assessment',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
