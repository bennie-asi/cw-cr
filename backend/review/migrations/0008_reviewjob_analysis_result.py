from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('review', '0007_modelconfig_request_timeout_s')]
    operations = [
        migrations.AddField(
            model_name='reviewjob', name='analysis_result',
            field=models.JSONField(null=True, blank=True, default=None),
        ),
    ]
