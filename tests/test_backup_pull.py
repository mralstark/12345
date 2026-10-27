import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from deploy import pull_backup
from scripts.backup_integrity import sign, verify


def transport(monkeypatch, tmp_path, *, corrupt=False, name=None):
    remote = tmp_path / "remote"
    remote.mkdir()
    archive = remote / "bratstvo-2026-10-27T123000-123456.tar.gz.enc"
    archive.write_bytes(b"synthetic-encrypted-backup")
    key = tmp_path / "recovery-key"
    key.write_bytes(b"synthetic-recovery-key-for-tests-000")
    sign(archive, key)
    if corrupt:
        archive.write_bytes(b"tampered-backup")
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        if command[0] == "ssh":
            return SimpleNamespace(returncode=0, stdout=json.dumps({"name": name or archive.name, "age": 10}))
        source_name = command[-2].rsplit("/", 1)[1]
        shutil.copyfile(remote / source_name, command[-1])
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(pull_backup.subprocess, "run", run)
    return archive, key, calls


def test_only_authenticated_backup_is_published_locally(monkeypatch, tmp_path):
    archive, key, calls = transport(monkeypatch, tmp_path)
    destination = tmp_path / "private-local"
    pull_backup.pull("root@server.example", destination, key, tmp_path / "ssh-key")
    verify(destination / archive.name, key)
    assert json.loads((destination / "last-pull.json").read_text())["authenticated"] is True
    assert len(calls) == 3


def test_tampered_transfer_never_replaces_local_backup(monkeypatch, tmp_path):
    archive, key, _ = transport(monkeypatch, tmp_path, corrupt=True)
    destination = tmp_path / "private-local"
    with pytest.raises(ValueError):
        pull_backup.pull("root@server.example", destination, key, tmp_path / "ssh-key")
    assert not (destination / archive.name).exists()
    assert not list(destination.glob(".download-*"))


def test_remote_filename_cannot_escape_local_directory(monkeypatch, tmp_path):
    _, key, calls = transport(monkeypatch, tmp_path, name="../../outside.enc")
    with pytest.raises(ValueError):
        pull_backup.pull("root@server.example", tmp_path / "local", key, tmp_path / "ssh-key")
    assert len(calls) == 1
