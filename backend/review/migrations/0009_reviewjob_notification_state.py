from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('review', '0008_reviewjob_analysis_result')]
    operations = [migrations.AddField(
        model_name='reviewjob', name='notification_state',
        field=models.CharField(max_length=20, default='pending'),
    )]
