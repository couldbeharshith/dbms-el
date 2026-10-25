"""Simple, practical encryption at rest (AES-256-GCM).

- Screenshot pixels: encrypted before touching disk -> data/screenshots/<uuid>.enc
- OCR text (sensitive raw screen content): encrypted into the observations.ocr_encrypted bytea column.

The key is a random 32-byte file generated once at data/secret.key. This is a
hobby-project grade approach on purpose: everything runs locally on one PC.
"""

import os
import secrets
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from backend.app import config

_KEY_FILE = config.DATA_DIR / "secret.key"


def _load_key() -> bytes:
    if _KEY_FILE.exists():
        return _KEY_FILE.read_bytes()
    key = secrets.token_bytes(32)
    _KEY_FILE.write_bytes(key)
    try:
        os.chmod(_KEY_FILE, 0o600)
    except OSError:
        pass  # Windows ACLs handle this differently; best effort.
    return key


_aesgcm = AESGCM(_load_key())


def encrypt(plaintext: bytes) -> bytes:
    nonce = secrets.token_bytes(12)
    return nonce + _aesgcm.encrypt(nonce, plaintext, associated_data=None)


def decrypt(ciphertext: bytes) -> bytes:
    nonce, body = ciphertext[:12], ciphertext[12:]
    return _aesgcm.decrypt(nonce, body, associated_data=None)
