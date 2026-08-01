"""
Token encryption helpers using Fernet (symmetric encryption).
The secret key is read from FERNET_KEY env var (32-byte URL-safe base64-encoded
string). Generate a new key with:
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""

from cryptography.fernet import Fernet
from django.conf import settings


def _fernet() -> Fernet:
    return Fernet(settings.FERNET_KEY.encode())


def encrypt_token(plaintext: str) -> str:
    """Return the encrypted token as a unicode string."""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    """Return the decrypted plaintext token."""
    return _fernet().decrypt(ciphertext.encode()).decode()
