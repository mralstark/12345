"""Exercise real Linux archive/encryption round trips using synthetic data only."""
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

import pytest


pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or not all(shutil.which(x) for x in ("bash", "openssl", "tar")),
    reason="Production backup uses Linux GNU tar, bash and OpenSSL",
)
SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "backup.sh"


def backup_fixture(tmp_path):
    app = tmp_path / "app"
    (app / ".venv" / "bin").mkdir(parents=True)
    (app / ".venv" / "bin" / "python").symlink_to(sys.executable)
    (app / "config.py").write_text(
        "import os\nfrom pathlib import Path\nSTORAGE_DIR=Path(os.environ['STORAGE_DIR'])\n"
    )
    storage = tmp_path / "custom uploads"
    (storage / "photos").mkdir(parents=True)
    (storage / "photos" / "sample.png").write_bytes(b"synthetic-image")
    (storage / ".hidden").write_bytes(b"synthetic-hidden")
    (storage / "sample-link").symlink_to("./photos/sample.png")
    commands = tmp_path / "commands"
    commands.mkdir()
    # No PostgreSQL server or production credentials: only pg_dump is replaced.
    fake_sudo = commands / "sudo"
    fake_sudo.write_text("#!/bin/sh\nprintf synthetic-pg-dump\n")
    fake_sudo.chmod(0o700)
    key = tmp_path / "key"
    key.write_text("synthetic-backup-key-for-tests-only")
    return dict(os.environ, APP_DIR=str(app), STORAGE_DIR=str(storage),
                BACKUP_DIR=str(tmp_path / "backups"), BACKUP_KEY_FILE=str(key),
                BACKUP_REMOTE_DIR=str(tmp_path / "offsite"),
                PATH=str(commands) + os.pathsep + os.environ["PATH"])


def run_backup(env):
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, timeout=30)


def test_custom_storage_round_trip_keeps_restore_layout_and_private_modes(tmp_path):
    env = backup_fixture(tmp_path)
    remote = Path(env["BACKUP_REMOTE_DIR"])
    remote.mkdir()
    result = run_backup(env)
    assert result.returncode == 0, result.stderr.decode()
    archives = list(Path(env["BACKUP_DIR"]).glob("*.tar.gz.enc"))
    assert len(archives) == 1
    archive = archives[0]
    assert archive.stat().st_mode & 0o777 == 0o600
    assert archive.parent.stat().st_mode & 0o777 == 0o700
    assert (remote / archive.name).read_bytes() == archive.read_bytes()
    decrypted = subprocess.run([
        "openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-pass",
        "file:" + env["BACKUP_KEY_FILE"], "-in", str(archive),
    ], capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(decrypted), mode="r:gz") as outer:
        assert set(outer.getnames()) == {"database.dump", "storage.tar.gz"}
        assert outer.extractfile("database.dump").read() == b"synthetic-pg-dump"
        with tarfile.open(fileobj=io.BytesIO(outer.extractfile("storage.tar.gz").read()), mode="r:gz") as inner:
            assert inner.extractfile("storage/photos/sample.png").read() == b"synthetic-image"
            assert inner.extractfile("storage/.hidden").read() == b"synthetic-hidden"
            assert inner.getmember("storage/sample-link").linkname == "./photos/sample.png"
            assert all(name == "storage" or name.startswith("storage/") for name in inner.getnames())
    assert not list(archive.parent.glob(".work.*"))


@pytest.mark.parametrize("invalid", ["missing_app", "missing_storage", "local_inside_storage", "remote_inside_storage"])
def test_backup_rejects_missing_source_and_recursive_destinations(tmp_path, invalid):
    env = backup_fixture(tmp_path)
    if invalid == "missing_app":
        env["APP_DIR"] = str(tmp_path / "does-not-exist")
    elif invalid == "missing_storage":
        env["STORAGE_DIR"] = str(tmp_path / "does-not-exist")
    else:
        key = "BACKUP_DIR" if invalid == "local_inside_storage" else "BACKUP_REMOTE_DIR"
        env[key] = str(Path(env["STORAGE_DIR"]) / "backups")
    result = run_backup(env)
    assert result.returncode != 0
    assert not list(tmp_path.rglob("*.tar.gz.enc"))
