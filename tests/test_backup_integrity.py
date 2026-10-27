from pathlib import Path
import pytest

from scripts.backup_integrity import sign, verify


def backup(tmp_path):
    archive = tmp_path / "bratstvo-test.tar.gz.enc"
    archive.write_bytes(b"Salted__synthetic-encrypted-content" * 60000)
    key = tmp_path / "key"
    key.write_bytes(b"synthetic-key-only-for-tests-000000000")
    sign(archive, key)
    return archive, key


def test_authenticated_archive_round_trip(tmp_path):
    archive, key = backup(tmp_path)
    verify(archive, key)


@pytest.mark.parametrize("corruption", ["ciphertext", "truncated", "key", "name", "manifest", "missing"])
def test_changed_backup_is_rejected_before_restore(tmp_path, corruption):
    archive, key = backup(tmp_path)
    tag = Path(str(archive) + ".hmac")
    if corruption == "ciphertext":
        data = bytearray(archive.read_bytes()); data[100] ^= 1; archive.write_bytes(data)
    elif corruption == "truncated":
        archive.write_bytes(archive.read_bytes()[:-1])
    elif corruption == "key":
        key.write_bytes(b"another-synthetic-key-for-tests-000000")
    elif corruption == "name":
        replacement = archive.with_name("another.enc")
        archive.rename(replacement); tag.rename(str(replacement) + ".hmac"); archive = replacement
    elif corruption == "manifest":
        tag.write_text('{"version":1,"archive":"invalid","hmac_sha256":"invalid"}')
    else:
        tag.unlink()
    with pytest.raises((ValueError, OSError)):
        verify(archive, key)
