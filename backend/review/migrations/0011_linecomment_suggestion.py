from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('review', '0010_review_summary_sync_status')]
    operations = [migrations.AddField(
        model_name='linecomment', name='suggestion',
        field=models.JSONField(blank=True, default=None, null=True),
    )]
