import hashlib
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings


def _key():
    return hashlib.sha256(settings.BIOMETRIC_KEY.encode()).digest()


def seal(data: bytes) -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(_key()).encrypt(nonce, data, None)


def open_sealed(data: bytes) -> bytes:
    return AESGCM(_key()).decrypt(data[:12], data[12:], None)
