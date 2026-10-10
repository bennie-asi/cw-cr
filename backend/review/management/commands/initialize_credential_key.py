import csv
import os
from pathlib import Path
import re
import subprocess

from cryptography.fernet import Fernet
from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from review.credential_crypto import (
    CIPHERTEXT_NAMESPACE, CREDENTIAL_COLUMNS, CredentialEncryptionError,
    configured_fernet, credential_key_file, validate_key,
)


def database_has_ciphertext(connection):
    database = connection.settings_dict['NAME']
    if connection.vendor == 'sqlite' and database != ':memory:' and not str(database).startswith('file:') and not Path(database).exists():
        return False
    tables = set(connection.introspection.table_names())
    quote = connection.ops.quote_name
    with connection.cursor() as cursor:
        for name, fields in CREDENTIAL_COLUMNS:
            model = apps.get_model('review', name)
            if model._meta.db_table not in tables:
                continue
            for field in fields:
                cursor.execute(
                    f'SELECT 1 FROM {quote(model._meta.db_table)} '
                    f'WHERE {quote(model._meta.get_field(field).column)} LIKE %s LIMIT 1',
                    [CIPHERTEXT_NAMESPACE + '%'],
                )
                if cursor.fetchone():
                    return True
    return False


def restrict_key_file_permissions(path, descriptor):
    if os.name != 'nt':
        os.fchmod(descriptor, 0o600)
        return
    identity = subprocess.run(['whoami', '/user', '/fo', 'csv', '/nh'], check=True, capture_output=True, text=True)
    row = next(csv.reader(identity.stdout.splitlines()))
    sid = row[-1].strip()
    if not re.fullmatch(r'S-1-(?:\d+-)+\d+', sid):
        raise CommandError('Could not identify the current Windows user for key file protection.')
    subprocess.run(
        ['icacls', str(path), '/inheritance:r', '/grant:r', f'*{sid}:(F)', '*S-1-5-18:(F)'],
        check=True, capture_output=True, text=True,
    )


class Command(BaseCommand):
    help = 'Explicitly initialize a protected credential encryption key without replacing an existing key.'

    def add_arguments(self, parser):
        parser.add_argument('--database', default='default', choices=tuple(connections))

    def handle(self, *args, **options):
        from django.conf import settings
        if settings.CREDENTIAL_ENCRYPTION_KEY:
            try:
                validate_key(settings.CREDENTIAL_ENCRYPTION_KEY)
            except CredentialEncryptionError as exc:
                raise CommandError(str(exc)) from None
            self.stdout.write('Configured environment key is valid; no key file was created.')
            return
        path = credential_key_file()
        if path.exists():
            self.validate_existing(path)
            return
        if database_has_ciphertext(connections[options['database']]):
            raise CommandError(
                'Cannot generate a replacement key for existing encrypted credentials. '
                'Restore the original key file or configure the original environment key.'
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            self.validate_existing(path)
            return
        except OSError:
            raise CommandError('Could not create the credential key file.') from None
        try:
            # Protect the empty file before writing any key material.
            restrict_key_file_permissions(path, descriptor)
            with os.fdopen(descriptor, 'wb') as destination:
                descriptor = None
                destination.write(Fernet.generate_key() + b'\n')
                destination.flush()
                os.fsync(destination.fileno())
        except (OSError, subprocess.SubprocessError, CommandError, StopIteration, IndexError):
            if descriptor is not None:
                os.close(descriptor)
            path.unlink(missing_ok=True)
            raise CommandError('Could not securely initialize the credential key file.') from None
        self.stdout.write(f'Credential key initialized: {path}')

    def validate_existing(self, path):
        try:
            configured_fernet()
        except CredentialEncryptionError as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(f'Existing credential key is valid and unchanged: {path}')
