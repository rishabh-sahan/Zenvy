"""Encryption of consultation audio at rest (AES-256-GCM).

File layout:  b"ZNV1" | 12-byte random nonce | ciphertext + 16-byte tag

The consultation id is bound in as associated data, so an encrypted file that
is copied under another consultation's name will not decrypt.
"""

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import settings

MAGIC = b"ZNV1"
NONCE_BYTES = 12


class EncryptionNotConfigured(RuntimeError):
    """AUDIO_ENCRYPTION_KEY is missing or not a valid 32-byte base64 key."""


class DecryptionFailed(RuntimeError):
    """The file is corrupted, tampered with, or was written with another key."""


def _key() -> bytes:
    raw = settings.AUDIO_ENCRYPTION_KEY
    if not raw:
        raise EncryptionNotConfigured(
            "AUDIO_ENCRYPTION_KEY is not set, so recordings cannot be stored safely."
        )
    try:
        key = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise EncryptionNotConfigured("AUDIO_ENCRYPTION_KEY is not valid base64.") from exc
    if len(key) != 32:
        raise EncryptionNotConfigured("AUDIO_ENCRYPTION_KEY must decode to exactly 32 bytes.")
    return key


def encrypt_bytes(plain: bytes, bind_to: str) -> bytes:
    nonce = os.urandom(NONCE_BYTES)
    return MAGIC + nonce + AESGCM(_key()).encrypt(nonce, plain, bind_to.encode())


def decrypt_bytes(blob: bytes, bind_to: str) -> bytes:
    if not blob.startswith(MAGIC) or len(blob) < len(MAGIC) + NONCE_BYTES + 16:
        raise DecryptionFailed("Not an encrypted Zenvy recording.")
    nonce = blob[len(MAGIC): len(MAGIC) + NONCE_BYTES]
    try:
        return AESGCM(_key()).decrypt(nonce, blob[len(MAGIC) + NONCE_BYTES:], bind_to.encode())
    except InvalidTag as exc:
        raise DecryptionFailed("The recording could not be decrypted.") from exc
