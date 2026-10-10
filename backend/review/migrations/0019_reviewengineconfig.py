# Generated for the multi-engine review configuration split.

from django.db import migrations, models


ENGINE_CHOICES = [
    ('builtin', '智审台内置评审'),
    ('pr-agent', 'PR-Agent'),
    ('open-code-review', 'OpenCodeReview'),
    ('pi-agent', 'Pi Agent'),
]


def move_engine_configuration(apps, schema_editor):
    database = schema_editor.connection.alias
    Project = apps.get_model('review', 'Project')
    ProjectPolicy = apps.get_model('review', 'ProjectPolicy')
    ModelConfig = apps.get_model('review', 'ModelConfig')
    ReviewEngineConfig = apps.get_model('review', 'ReviewEngineConfig')

    engine_config, _ = ReviewEngineConfig.objects.using(database).get_or_create(pk=1)
    global_model = ModelConfig.objects.using(database).filter(scope='global', project__isnull=True).first()
    default_engine = getattr(global_model, 'engine', 'builtin') or 'builtin'
    if default_engine not in {choice[0] for choice in ENGINE_CHOICES}:
        default_engine = 'builtin'
    engine_config.default_engine = default_engine
    engine_config.save(using=database)

    for project in Project.objects.using(database).all().iterator():
        policy = ProjectPolicy.objects.using(database).filter(project_id=project.pk).first()
        if policy is None:
            continue
        project_model = ModelConfig.objects.using(database).filter(project_id=project.pk).first()
        policy_engine = getattr(policy, 'engine', 'builtin') or 'builtin'
        model_engine = getattr(project_model, 'engine', 'builtin') or 'builtin'
        candidates = [value for value in (policy_engine, model_engine) if value in {choice[0] for choice in ENGINE_CHOICES}]
        effective_engine = next((value for value in candidates if value != default_engine), default_engine)
        policy.engine = effective_engine
        policy.save(using=database, update_fields=['engine'])
        project.engine_inherit = effective_engine == default_engine
        project.save(using=database, update_fields=['engine_inherit'])


class Migration(migrations.Migration):

    dependencies = [
        ('review', '0018_alter_modelconfig_engine_alter_projectpolicy_engine'),
    ]

    operations = [
        migrations.CreateModel(
            name='ReviewEngineConfig',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                ('default_engine', models.CharField(choices=ENGINE_CHOICES, default='builtin', max_length=80)),
                ('verified_engines', models.JSONField(blank=True, default=dict)),
            ],
            options={'abstract': False},
        ),
        migrations.AddField(
            model_name='project',
            name='engine_inherit',
            field=models.BooleanField(default=True),
        ),
        migrations.AlterField(
            model_name='projectpolicy',
            name='engine',
            field=models.CharField(choices=ENGINE_CHOICES, default='builtin', max_length=80),
        ),
        migrations.RunPython(move_engine_configuration, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='modelconfig',
            name='engine',
        ),
        migrations.AlterField(
            model_name='linecomment',
            name='level',
            field=models.CharField(choices=[('P0', 'P0'), ('P1', 'P1'), ('P2', 'P2'), ('UNRATED', '未评级')], default='P2', max_length=10),
        ),
        migrations.AddField(
            model_name='review',
            name='engine_id',
            field=models.CharField(default='builtin', max_length=80),
        ),
        migrations.AddField(
            model_name='review',
            name='engine_version',
            field=models.CharField(blank=True, default='', max_length=160),
        ),
        migrations.AddField(
            model_name='review',
            name='engine_model',
            field=models.CharField(blank=True, default='', max_length=160),
        ),
    ]
