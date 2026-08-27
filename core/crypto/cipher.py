"""
Cipher utilities.

  RSA-OAEP  -> static credentials: api_key / pin / totp
  Fernet    -> session tokens:     jwt / feed / refresh

Keys come from settings (env): PUBLIC_KEY, PRIVATE_KEY (PEM), FERNET.
"""

from __future__ import annotations

import base64
from functools import lru_cache

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from django.conf import settings

_OAEP = padding.OAEP(
    mgf=padding.MGF1(algorithm=hashes.SHA256()),
    algorithm=hashes.SHA256(),
    label=None,
)


@lru_cache(maxsize=1)
def _public_key():
    if not settings.PUBLIC_KEY:
        raise RuntimeError("ANGEL_PUBLIC (public key) is not configured")
    return serialization.load_pem_public_key(settings.PUBLIC_KEY.encode())


@lru_cache(maxsize=1)
def _private_key():
    if not settings.PRIVATE_KEY:
        raise RuntimeError("ANGEL_PRIVATE (private key) is not configured")
    return serialization.load_pem_private_key(settings.PRIVATE_KEY.encode(), password=None)


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    if not settings.FERNET:
        raise RuntimeError("FERNET key is not configured")
    return Fernet(settings.FERNET)


# --- RSA-OAEP: api_key / pin / totp ---
def encrypt_with_public_key(plaintext: str) -> str:
    ciphertext = _public_key().encrypt(plaintext.encode("utf-8"), _OAEP)
    return base64.b64encode(ciphertext).decode("utf-8")


def decrypt_with_private_key(b64_ciphertext: str) -> str:
    ciphertext = base64.b64decode(b64_ciphertext)
    return _private_key().decrypt(ciphertext, _OAEP).decode("utf-8")


# --- Fernet: jwt / feed / refresh tokens ---
def encrypt_token(token: str) -> str:
    return _fernet().encrypt(token.encode()).decode()


def decrypt_token(enc: str) -> str:
    return _fernet().decrypt(enc.encode()).decode()
