from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('review', '0009_reviewjob_notification_state')]
    operations = [migrations.AddField(model_name='review', name='summary_sync_status',
        field=models.CharField(max_length=20, default='pending'))]
