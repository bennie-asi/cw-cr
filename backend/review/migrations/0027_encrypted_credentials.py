from django.db import migrations

import review.credential_fields
from review.credential_crypto import decrypt_credential, encrypt_credential


# This migration's historical schema must not grow when runtime credentials do.
CREDENTIAL_COLUMNS = (
    ('Project', ('webhook_secret',)),
    ('ModelConfig', ('api_key',)),
    ('GitLabConfig', ('api_token', 'webhook_secret')),
    ('NotificationConfig', ('webhook_url',)),
)


def rewrite_credentials(apps, schema_editor, transform):
    connection = schema_editor.connection
    quote = connection.ops.quote_name
    # Raw parameterized updates avoid encrypted field preparation on rollback
    # and leave created_at/updated_at and all unrelated columns untouched.
    with connection.cursor() as cursor:
        for name, fields in CREDENTIAL_COLUMNS:
            model = apps.get_model('review', name)
            table = quote(model._meta.db_table)
            pk = quote(model._meta.pk.column)
            for field in fields:
                column = quote(model._meta.get_field(field).column)
                cursor.execute(f'SELECT {pk}, {column} FROM {table} WHERE {column} <> %s', [''])
                rows = cursor.fetchall()
                for identity, stored in rows:
                    transformed = transform(stored)
                    if transformed != stored:
                        cursor.execute(f'UPDATE {table} SET {column} = %s WHERE {pk} = %s', [transformed, identity])


def encrypt_existing_credentials(apps, schema_editor):
    rewrite_credentials(apps, schema_editor, encrypt_credential)


def decrypt_existing_credentials(apps, schema_editor):
    rewrite_credentials(apps, schema_editor, decrypt_credential)


class Migration(migrations.Migration):
    dependencies = [('review', '0026_repository_identity')]

    operations = [
        migrations.AlterField(model_name='project', name='webhook_secret', field=review.credential_fields.EncryptedCharField(blank=True, max_length=255)),
        migrations.AlterField(model_name='modelconfig', name='api_key', field=review.credential_fields.EncryptedCharField(blank=True, default='', max_length=255)),
        migrations.AlterField(model_name='gitlabconfig', name='api_token', field=review.credential_fields.EncryptedCharField(blank=True, default='', max_length=255)),
        migrations.AlterField(model_name='gitlabconfig', name='webhook_secret', field=review.credential_fields.EncryptedCharField(blank=True, default='', max_length=255)),
        migrations.AlterField(model_name='notificationconfig', name='webhook_url', field=review.credential_fields.EncryptedURLField(blank=True, default='')),
        migrations.RunPython(encrypt_existing_credentials, decrypt_existing_credentials),
    ]
