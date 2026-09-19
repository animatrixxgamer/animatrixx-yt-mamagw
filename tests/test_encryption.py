"""Unit tests for encryption service."""

import pytest

from src.services.encryption import EncryptionService


class TestEncryptionService:
    """Test encryption and decryption."""

    def setup_method(self):
        self.service = EncryptionService()

    def test_encrypt_decrypt_string(self):
        """Test basic encryption and decryption."""
        original = "test-secret-token-12345"
        encrypted = self.service.encrypt(original)
        decrypted = self.service.decrypt(encrypted)

        assert encrypted != original
        assert decrypted == original

    def test_encrypt_decrypt_empty_string(self):
        """Test encryption of empty string."""
        original = ""
        encrypted = self.service.encrypt(original)
        decrypted = self.service.decrypt(encrypted)

        assert decrypted == original

    def test_encrypt_decrypt_unicode(self):
        """Test encryption of unicode characters."""
        original = "test-日本語-token"
        encrypted = self.service.encrypt(original)
        decrypted = self.service.decrypt(encrypted)

        assert decrypted == original

    def test_encrypt_decrypt_long_string(self):
        """Test encryption of long string."""
        original = "a" * 10000
        encrypted = self.service.encrypt(original)
        decrypted = self.service.decrypt(encrypted)

        assert decrypted == original

    def test_encrypt_decrypt_dict(self):
        """Test encryption and decryption of dictionary."""
        original = {
            "access_token": "ya29.test-token",
            "refresh_token": "1//test-refresh",
            "expires_at": "2024-01-01T00:00:00Z",
        }
        encrypted = self.service.encrypt_dict(original)
        decrypted = self.service.decrypt_dict(encrypted)

        assert decrypted == original

    def test_different_encryptions_produce_different_output(self):
        """Test that same input produces different encrypted output (randomness)."""
        original = "test"
        encrypted1 = self.service.encrypt(original)
        encrypted2 = self.service.encrypt(original)

        # Should be different due to random IV
        assert encrypted1 != encrypted2

    def test_decrypt_with_wrong_key_fails(self):
        """Test that decryption with wrong key fails."""
        from cryptography.fernet import InvalidToken

        original = "test"
        encrypted = self.service.encrypt(original)

        # A service built on a DIFFERENT key must not be able to read the data.
        # (Previously this constructed a second service with no arguments, which
        # silently reused the same settings key and therefore never failed.)
        wrong_service = EncryptionService(key="a-completely-different-master-key")
        with pytest.raises(InvalidToken):
            wrong_service.decrypt(encrypted)

    def test_default_key_is_stable_across_instances(self):
        """Test the key is derived from settings, not generated per instance.

        Tokens are persisted encrypted and must stay readable after a restart,
        so a second service on the same settings must decrypt the first's output.
        """
        original = "persisted-refresh-token"
        first = EncryptionService()
        second = EncryptionService()

        assert second.decrypt(first.encrypt(original)) == original
