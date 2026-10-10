from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('review', '0022_linecomment_anchor_code')]

    operations = [
        migrations.AddField(model_name='projectpolicy', name='block_on_p2', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='projectpolicy', name='security_prompt', field=models.TextField(blank=True, default='')),
        migrations.AddField(model_name='projectpolicy', name='reliability_prompt', field=models.TextField(blank=True, default='')),
        migrations.AddField(model_name='projectpolicy', name='style_prompt', field=models.TextField(blank=True, default='')),
    ]
