from __future__ import annotations
import pytest
from cryptography.fernet import InvalidToken
from django.core.exceptions import ImproperlyConfigured
from workouts.crypto import (
    encrypt_value,
    decrypt_value,
    EncryptedTextField,
    _get_fernet,
)
import os
from cryptography.fernet import Fernet

@pytest.fixture(autouse=True)
def setup_test_fernet_key(monkeypatch):
    # We generate a valid Fernet key specifically for tests and inject it into env vars.
    # This isolates tests from local .env files.
    test_key = Fernet.generate_key().decode()
    monkeypatch.setenv('FERNET_KEY', test_key)


class TestEncryptDecryptRoundTrip:
    #Pure crypto layer tests without DB operations.

    def test_round_trip_preserves_plaintext(self):
        plaintext = "garmin_access_token_abc123_XYZ"
        ciphertext = encrypt_value(plaintext)
        
        assert ciphertext != plaintext, "Encryption must modify the original value"
        assert decrypt_value(ciphertext) == plaintext

    def test_same_plaintext_produces_different_ciphertext_each_time(self):
        # Fernet uses a random InitializationVector(random-salt), so identical plaintext must encrypt to different ciphertext.
        # This prevents attackers from correlating identical tokens across different users.
        plaintext = "same_token_value"
        c1 = encrypt_value(plaintext)
        c2 = encrypt_value(plaintext)
        
        assert c1 != c2, "Encryption must be non-deterministic"
        assert decrypt_value(c1) == plaintext
        assert decrypt_value(c2) == plaintext

    def test_encrypt_returns_none_unchanged(self):
        assert encrypt_value(None) is None

    def test_encrypt_returns_empty_string_unchanged(self):
        assert encrypt_value('') == ''

    def test_encrypt_coerces_non_string_to_string(self):
        # Defensive check: ensure int are coerced to string without crashing
        ciphertext = encrypt_value(12345)
        assert decrypt_value(ciphertext) == '12345'

    def test_decrypt_returns_none_unchanged(self):
        assert decrypt_value(None) is None

    def test_decrypt_returns_empty_string_unchanged(self):
        assert decrypt_value('') == ''

    def test_decrypt_raises_on_corrupted_ciphertext(self):
        # Fail-loud pattern: invalid ciphertext must raise an exception immediately
        with pytest.raises(InvalidToken):
            decrypt_value("this_is_not_valid_fernet_ciphertext")


class TestFernetConfiguration:
    """Testing application behavior during environment misconfigurations."""

    def test_raises_when_fernet_key_is_missing(self, monkeypatch):
        # Simulate missing env variable
        monkeypatch.delenv('FERNET_KEY', raising=False)
        with pytest.raises(ImproperlyConfigured, match='FERNET_KEY'):
            _get_fernet()

    def test_raises_when_fernet_key_is_invalid(self, monkeypatch):
        # Simulate malformed base64 or wrong key length
        monkeypatch.setenv('FERNET_KEY', 'not_a_valid_fernet_key')
        with pytest.raises(ImproperlyConfigured, match='Invalid FERNET_KEY'):
            _get_fernet()


class TestEncryptedTextField:
    """Django custom field integration testing."""

    def test_get_prep_value_encrypts_on_write(self):
        # Must return ciphertext before saving to DB
        field = EncryptedTextField()
        plaintext = "strava_refresh_token_xyz"
        prep_value = field.get_prep_value(plaintext)
        
        assert prep_value != plaintext
        assert decrypt_value(prep_value) == plaintext

    def test_get_prep_value_returns_none_for_none(self):
        field = EncryptedTextField()
        assert field.get_prep_value(None) is None

    def test_from_db_value_decrypts_on_read(self):
        # Must return decrypted plaintext when loading from DB
        field = EncryptedTextField()
        plaintext = "oura_access_token"
        ciphertext = encrypt_value(plaintext)
        result = field.from_db_value(ciphertext, expression=None, connection=None)
        
        assert result == plaintext

    def test_from_db_value_returns_none_for_null_column(self):
        field = EncryptedTextField()
        assert field.from_db_value(None, expression=None, connection=None) is None