import os
import time
import ssl
from pathlib import Path

from scripts.operations_watch import backup_fresh, record_state, tls_context, health_url
from utils.bot_health import BotHealth


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


def test_monitor_requires_the_current_systemd_process_and_live_polling(tmp_path,monkeypatch):
    import sys
    from types import SimpleNamespace
    from scripts import operations_watch
    monkeypatch.setenv("INVOCATION_ID","a"*32)
    health=BotHealth(tmp_path/"health.json")
    health.data.update(phase="polling",last_poll_success_at=time.time(),setup_pending=False,notifier_running=True)
    health.write()
    monkeypatch.setattr("config.BOT_HEALTH_FILE",health.path)
    monkeypatch.setitem(sys.modules,"pwd",SimpleNamespace(getpwnam=lambda name:SimpleNamespace(pw_uid=os.getuid() if os.name=="posix" else 0)))
    properties=f"MainPID={os.getpid()}\nInvocationID={'a'*32}\nUser=synthetic\n"
    monkeypatch.setattr(operations_watch.subprocess,"run",lambda *args,**kwargs:SimpleNamespace(stdout=properties))
    assert operations_watch.bot_readiness()==[]
    health.data["last_poll_success_at"]=time.time()-200;health.write()
    assert operations_watch.bot_readiness()==["bot-polling-stale"]
    health.data["invocation_id"]="b"*32;health.write()
    assert operations_watch.bot_readiness()==["bot-state-unavailable"]
