"""Versioned authenticated encryption for database credentials.

Keys are supplied independently of Django's SECRET_KEY. This module never
creates keys; initialization is an explicit management command.
"""
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


CIPHERTEXT_PREFIX = 'credential:v1:'
CIPHERTEXT_NAMESPACE = 'credential:'
CREDENTIAL_COLUMNS = (
    ('Project', ('webhook_secret',)),
    ('ModelConfig', ('api_key',)),
    ('GitLabConfig', ('api_token', 'webhook_secret')),
    ('NotificationConfig', ('webhook_url',)),
)


class CredentialEncryptionError(ImproperlyConfigured):
    """A safe configuration error that never includes a key or credential."""


def validate_key(key):
    try:
        encoded = key.strip().encode('ascii') if isinstance(key, str) else key.strip()
        return Fernet(encoded)
    except (ValueError, TypeError, UnicodeError, AttributeError):
        raise CredentialEncryptionError('Credential encryption key is invalid; supply a valid Fernet key.') from None


def credential_key_file():
    return Path(settings.CREDENTIAL_ENCRYPTION_KEY_FILE)


def configured_fernet():
    key = settings.CREDENTIAL_ENCRYPTION_KEY
    if key:
        return validate_key(key)
    try:
        key = credential_key_file().read_bytes()
    except OSError:
        raise CredentialEncryptionError(
            'Credential encryption key file is unavailable. Restore the original key, '
            'or run initialize_credential_key before storing the first credential.'
        ) from None
    return validate_key(key)


def decrypt_credential(value):
    if value is None or value == '' or not value.startswith(CIPHERTEXT_NAMESPACE):
        return value
    if not value.startswith(CIPHERTEXT_PREFIX):
        raise CredentialEncryptionError('Stored credential encryption version is unsupported.')
    try:
        return configured_fernet().decrypt(value[len(CIPHERTEXT_PREFIX):].encode('ascii')).decode('utf-8')
    except (InvalidToken, UnicodeError, ValueError):
        raise CredentialEncryptionError(
            'Stored credential could not be decrypted. Restore the original encryption key '
            'and verify the database backup; the stored value was not used.'
        ) from None


def encrypt_credential(value):
    if value is None or value == '':
        return value
    if value.startswith(CIPHERTEXT_NAMESPACE):
        # Validate recognized storage values, including assignments from a raw
        # export, rather than double encrypting or forwarding damaged tokens.
        decrypt_credential(value)
        return value
    token = configured_fernet().encrypt(value.encode('utf-8')).decode('ascii')
    return CIPHERTEXT_PREFIX + token
