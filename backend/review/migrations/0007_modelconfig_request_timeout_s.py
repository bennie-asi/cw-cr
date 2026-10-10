from django.db import migrations, models
from django.core.validators import MinValueValidator, MaxValueValidator


class Migration(migrations.Migration):
    dependencies = [('review', '0006_alter_modelconfig_model_and_more')]
    operations = [migrations.AddField(model_name='modelconfig', name='request_timeout_s', field=models.PositiveIntegerField(default=120, validators=[MinValueValidator(10), MaxValueValidator(600)]))]
