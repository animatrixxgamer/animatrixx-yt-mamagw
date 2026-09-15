"""Encryption service for secure token storage."""

import base64

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from src.config import get_settings

settings = get_settings()


class EncryptionService:
    """Service for encrypting and decrypting sensitive data."""

    def __init__(self, key: str | None = None) -> None:
        """Create the service.

        Args:
            key: Master secret to derive the Fernet key from. Defaults to
                settings.token_encryption_key. Pass an explicit value to use a
                deliberately different key (e.g. to verify that data encrypted
                under another key cannot be read, or when rotating keys).
        """
        self._fernet = self._create_fernet(key)

    def _create_fernet(self, key: str | None = None) -> Fernet:
        """Create a Fernet instance from the given master secret."""
        master = key if key is not None else settings.token_encryption_key

        # Derive a proper key from the master secret
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"youtube-bot-salt",  # In production, use random salt per encryption
            iterations=100000,
        )
        derived = base64.urlsafe_b64encode(kdf.derive(master.encode()))
        return Fernet(derived)

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a string and return base64-encoded ciphertext."""
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt base64-encoded ciphertext and return plaintext."""
        return self._fernet.decrypt(ciphertext.encode()).decode()

    def encrypt_dict(self, data: dict) -> str:
        """Encrypt a dictionary as JSON."""
        import json
        return self.encrypt(json.dumps(data))

    def decrypt_dict(self, ciphertext: str) -> dict:
        """Decrypt ciphertext back to dictionary."""
        import json
        return json.loads(self.decrypt(ciphertext))


# Singleton instance
encryption_service = EncryptionService()
