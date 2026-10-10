from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('review', '0021_global_prompt_config')]
    operations = [migrations.AddField(model_name='linecomment', name='anchor_code',
                                     field=models.TextField(blank=True, default=''))]
