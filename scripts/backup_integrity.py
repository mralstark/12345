"""Encrypt-then-MAC companion for existing OpenSSL-encrypted backup archives.

Verify this authentication file before decrypting or restoring an archive.
Uses a domain-separated key and bounded-memory reads; never prints key material.
"""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path


def archive_mac(archive: Path, key_file: Path) -> str:
    with key_file.open("rb") as stream:
        key = stream.read(4097).strip()
    if not 32 <= len(key) <= 4096:
        raise ValueError("Backup key must contain 32–4096 bytes")
    mac_key = hmac.digest(key, b"bratstvo-backup-authentication-v1", "sha256")
    authenticator = hmac.new(mac_key, digestmod=hashlib.sha256)
    authenticator.update(archive.name.encode("utf-8") + b"\0")
    with archive.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            authenticator.update(chunk)
    return authenticator.hexdigest()


def sign(archive: Path, key_file: Path) -> Path:
    target = archive.with_name(archive.name + ".hmac")
    content = json.dumps({"version": 1, "archive": archive.name,
                          "hmac_sha256": archive_mac(archive, key_file)}) + "\n"
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(content)
    return target


def verify(archive: Path, key_file: Path) -> None:
    target = archive.with_name(archive.name + ".hmac")
    with target.open("rb") as stream:
        content = stream.read(4097)
    if len(content) > 4096:
        raise ValueError("Invalid backup authentication file")
    manifest = json.loads(content)
    if (not isinstance(manifest, dict) or set(manifest) != {"version", "archive", "hmac_sha256"}
            or manifest["version"] != 1 or manifest["archive"] != archive.name
            or not isinstance(manifest["hmac_sha256"], str)
            or not hmac.compare_digest(manifest["hmac_sha256"], archive_mac(archive, key_file))):
        raise ValueError("Backup authentication failed; do not decrypt or restore")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("sign", "verify"))
    parser.add_argument("archive", type=Path)
    parser.add_argument("--key-file", required=True, type=Path)
    args = parser.parse_args()
    try:
        (sign if args.operation == "sign" else verify)(args.archive, args.key_file)
    except (OSError, ValueError, TypeError) as exc:
        raise SystemExit("Backup integrity operation failed: " + type(exc).__name__) from None
    print("Backup authentication: OK")
