"""Plaintext CharField/URLField semantics with encrypted SQL text storage."""
from django.db import models

from review.credential_crypto import decrypt_credential, encrypt_credential


class EncryptedCredentialMixin:
    def db_type(self, connection):
        # Ciphertexts exceed the original plaintext limit, especially for UTF-8
        # secrets. Keep max_length for validation but use TEXT on every backend.
        return models.TextField().db_type(connection)

    def cast_db_type(self, connection):
        return self.db_type(connection)

    def from_db_value(self, value, expression, connection):
        return decrypt_credential(value)

    def to_python(self, value):
        return decrypt_credential(super().to_python(value))

    def get_prep_value(self, value):
        return encrypt_credential(super().get_prep_value(value))


class EncryptedCharField(EncryptedCredentialMixin, models.CharField):
    pass


class EncryptedURLField(EncryptedCredentialMixin, models.URLField):
    pass
