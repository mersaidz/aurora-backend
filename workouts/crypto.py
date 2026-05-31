import os
import logging
from django.db import models
from django.core.exceptions import ImproperlyConfigured
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


def _get_fernet() -> Fernet:
    key = os.getenv('FERNET_KEY')
    if not key:
        raise ImproperlyConfigured("FERNET_KEY is not set in environment variables")
    try:
        return Fernet(key.encode())
    except Exception as e:
        raise ImproperlyConfigured(f"Invalid FERNET_KEY: {e}")


def encrypt_value(value):
    #Encrypt plaintext value before storing in DB.
    if value is None or value == '':
        return value
    if not isinstance(value, str):
        value = str(value)
    return _get_fernet().encrypt(value.encode()).decode()


def decrypt_value(value):
    #Decrypt DB-stored value reverse to plain text
    if value is None or value == '':
        return value
    try:
        return _get_fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        # Loud failure — silently returning None hides corruption / key rotation problems.
        logger.exception("Failed to decrypt EncryptedTextField value")
        raise


class EncryptedTextField(models.TextField):
    """TextField that transparently encrypts on write and decrypts on read via Fernet."""

    description = "Symmetrically encrypted text field (Fernet)"

    def from_db_value(self, value, expression, connection):
        if value is None:
            return None
        return decrypt_value(value)

    def to_python(self, value):
        # Called on form/deserialization input, at that point we already have plaintext.
        return value

    def get_prep_value(self, value):
        if value is None:
            return None
        return encrypt_value(value)

    def value_to_string(self, obj):
        # Used by serializers (dumpdata). Return ciphertext so dumps don't leak plaintext.
        value = self.value_from_object(obj)
        return self.get_prep_value(value)
