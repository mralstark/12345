import os
import time
import ssl
from pathlib import Path

from scripts.operations_watch import backup_fresh, record_state, tls_context, health_url


def test_monitor_requires_authenticated_modern_tls_and_https():
    context = tls_context()
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    assert health_url("file:///etc/passwd") is False
    assert health_url("http://example.test/healthz") is False


def test_backup_age_and_authentication_are_both_required(tmp_path):
    now = time.time()
    assert not backup_fresh(tmp_path, now)
    archive = tmp_path / "bratstvo-test.tar.gz.enc"
    archive.write_bytes(b"synthetic")
    assert not backup_fresh(tmp_path, now + 10)
    Path(str(archive) + ".hmac").write_bytes(b"synthetic-tag")
    assert backup_fresh(tmp_path, now + 10)
    assert not backup_fresh(tmp_path, now + 37 * 3600)
    os.utime(archive, (now + 100, now + 100))
    assert not backup_fresh(tmp_path, now)


def test_monitor_records_failure_and_recovery_without_sensitive_context(tmp_path, capsys):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    first = record_state(directory, ["database"], now=100)
    assert first["consecutive_failures"] == 1
    assert record_state(directory, ["database"], now=200)["consecutive_failures"] == 2
    recovered = record_state(directory, [], now=300)
    assert recovered["consecutive_failures"] == 0
    assert recovered["last_healthy_at"] == 300
    assert '"issues": []' in capsys.readouterr().out
