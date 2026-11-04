"""Versioned authenticated encryption; keys never belong in database backups.

AES-256-SIV uses 512-bit keys. Random associated nonces hide repeated prose;
lookup domains intentionally use deterministic SIV to retain exact joins/indexes.
That reveals equality (not plaintext) for identifiers, an explicit tradeoff.
"""
import base64
import binascii
import json
import hashlib
import os
import re
from functools import lru_cache
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESSIV

PREFIX = "bdb1:"
FILE_PREFIX = b"BRATSTVO-FILE-1\n"


class DataEncryptionError(RuntimeError):
    """Messages must never contain plaintext, keys, paths or ciphertext."""


def _decode(value: str) -> bytes:
    try:
        return base64.b64decode(value.encode("ascii"), altchars=b"-_", validate=True)
    except (ValueError, UnicodeError, binascii.Error) as exc:
        raise DataEncryptionError("Invalid encrypted-data encoding") from None


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii")


@lru_cache(maxsize=1)
def keyring() -> tuple[str, dict[str, bytes]]:
    credentials = os.environ.get("CREDENTIALS_DIRECTORY", "").strip()
    location = str(Path(credentials) / "data-keyring") if credentials else os.environ.get("DATA_ENCRYPTION_KEY_FILE", "").strip()
    if not location or not Path(location).is_absolute():
        raise DataEncryptionError("An absolute DATA_ENCRYPTION_KEY_FILE is required")
    path = Path(location)
    if path.is_symlink():
        raise DataEncryptionError("The encryption key must be a private regular file")
    try:
        resolved = path.resolve()
        forbidden = [Path(__file__).resolve().parents[1]]
        if os.environ.get("STORAGE_DIR"):
            forbidden.append(Path(os.environ["STORAGE_DIR"]).resolve())
        if any(resolved.is_relative_to(root) for root in forbidden):
            raise DataEncryptionError("Keep the encryption key outside source and storage directories")
        if os.name == "posix" and path.stat().st_mode & 0o077:
            raise DataEncryptionError("Encryption key permissions must be owner-only")
        if path.stat().st_size > 16_384:
            raise DataEncryptionError("Invalid encryption keyring")
        value = json.loads(path.read_text(encoding="utf-8"))
        active = value["active"]
        keys = {name: _decode(encoded) for name, encoded in value["keys"].items()}
        if not keys or len(keys) > 16 or active not in keys:
            raise ValueError
        if any(not re.fullmatch(r"[a-zA-Z0-9_-]{1,32}", name) or len(key) != 64 for name, key in keys.items()):
            raise ValueError
        return active, keys
    except DataEncryptionError:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise DataEncryptionError("Cannot load the private encryption keyring") from None


def encrypt_bytes(value: bytes, domain: str, *, deterministic: bool = False) -> str:
    active, keys = keyring()
    nonce = b"" if deterministic else os.urandom(16)
    mode = "d" if deterministic else "r"
    aad = [b"bratstvo-data-v1", domain.encode("utf-8"), active.encode("ascii"), mode.encode("ascii")]
    if nonce:
        aad.append(nonce)
    encrypted = AESSIV(keys[active]).encrypt(value, aad)
    return f"{PREFIX}{active}:{mode}:{_encode(nonce)}:{_encode(encrypted)}"


def decrypt_bytes(value: str, domain: str, *, deterministic: bool = False) -> bytes:
    try:
        version, name, mode, encoded_nonce, encoded_value = value.split(":")
        nonce = _decode(encoded_nonce)
        if version != "bdb1" or mode != ("d" if deterministic else "r") or len(nonce) != (0 if deterministic else 16):
            raise ValueError
        _, keys = keyring()
        key = keys[name]
        aad = [b"bratstvo-data-v1", domain.encode("utf-8"), name.encode("ascii"), mode.encode("ascii")]
        if nonce:
            aad.append(nonce)
        return AESSIV(key).decrypt(_decode(encoded_value), aad)
    except DataEncryptionError:
        raise
    except (InvalidTag, ValueError, KeyError, TypeError, UnicodeError, AttributeError):
        raise DataEncryptionError("Encrypted data failed authentication") from None


def encrypt_file_bytes(value: bytes, relative_path: str) -> bytes:
    active, keys = keyring()
    name = active.encode("ascii")
    nonce = os.urandom(16)
    aad = [FILE_PREFIX, relative_path.encode("utf-8"), name, nonce]
    return FILE_PREFIX + bytes([len(name)]) + name + nonce + AESSIV(keys[active]).encrypt(value, aad)


def decrypt_file_bytes(value: bytes, relative_path: str) -> bytes:
    try:
        if not value.startswith(FILE_PREFIX):
            raise ValueError
        offset = len(FILE_PREFIX)
        length = value[offset]
        if not 1 <= length <= 32:
            raise ValueError
        name = value[offset + 1:offset + 1 + length]
        nonce = value[offset + 1 + length:offset + 17 + length]
        cipher = value[offset + 17 + length:]
        if len(nonce) != 16 or len(cipher) < 16:
            raise ValueError
        _, keys = keyring()
        return AESSIV(keys[name.decode("ascii")]).decrypt(cipher, [FILE_PREFIX, relative_path.encode("utf-8"), name, nonce])
    except DataEncryptionError:
        raise
    except (InvalidTag, ValueError, KeyError, TypeError, UnicodeError, IndexError):
        raise DataEncryptionError("Encrypted file failed authentication") from None


def private_log_ref(value: int) -> str:
    """Correlate delivery failures without writing Telegram identifiers to logs."""
    try:
        sealed = encrypt_bytes(str(value).encode("ascii"), "log-recipient", deterministic=True)
        return hashlib.sha256(sealed.encode("ascii")).hexdigest()[:12]
    except DataEncryptionError:
        return "unavailable"
