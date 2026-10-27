"""Read-only operational checks. Emit codes only, never URLs, tokens or PII."""
import argparse
import asyncio
import http.client
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
from urllib.parse import urlsplit


def health_url(url):
    connection = None
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return False
        connection = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443,
                                                timeout=8, context=ssl.create_default_context())
        connection.request("GET", parsed.path or "/")
        response = connection.getresponse()
        return response.status == 200 and json.loads(response.read(1024)) == {"status": "ok"}
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        if connection is not None:
            connection.close()


def certificate_days(hostname):
    with socket.create_connection((hostname, 443), timeout=5) as raw:
        with ssl.create_default_context().wrap_socket(raw, server_hostname=hostname) as channel:
            return (ssl.cert_time_to_seconds(channel.getpeercert()["notAfter"]) - time.time()) / 86400


def backup_fresh(directory, now=None):
    now = time.time() if now is None else now
    files = [p for p in directory.glob("bratstvo-*.tar.gz.enc") if p.is_file() and not p.is_symlink()]
    if not files:
        return False
    newest = max(files, key=lambda p: p.stat().st_mtime)
    age = now - newest.stat().st_mtime
    return 0 <= age <= 36 * 3600 and Path(str(newest) + ".hmac").is_file()


async def database_available():
    from sqlalchemy import text
    from database.db import engine
    try:
        async def query():
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        await asyncio.wait_for(query(), timeout=5)
        return True
    except Exception:
        return False
    finally:
        await engine.dispose()


async def telegram_available():
    from utils.notify import get_notifier_bot, close_notifier_bot
    try:
        bot = get_notifier_bot()
        return bot is not None and bool(await bot.get_me(request_timeout=8))
    except Exception:
        return False
    finally:
        await close_notifier_bot()


def collect(backup_dir):
    from config import BASE_DIR, STORAGE_DIR, WEBAPP_URL, validate_security_config
    issues = []
    try:
        validate_security_config()
    except (ValueError, RuntimeError):
        issues.append("configuration")
    for unit in ("bratstvo-api", "bratstvo-bot", "nginx", "postgresql"):
        if subprocess.run(["systemctl", "is-active", "--quiet", unit], timeout=5).returncode:
            issues.append("service:" + unit)
    if not health_url((WEBAPP_URL or "").rstrip("/") + "/healthz"):
        issues.append("https-health")
    try:
        if certificate_days(urlsplit(WEBAPP_URL).hostname) < 21:
            issues.append("tls-expiry")
    except (OSError, ValueError, KeyError, TypeError):
        issues.append("tls-validation")
    if not asyncio.run(database_available()):
        issues.append("database")
    if not asyncio.run(telegram_available()):
        issues.append("telegram-transport")
    usage = shutil.disk_usage(STORAGE_DIR)
    if usage.free < max(512 * 1024 * 1024, usage.total * 0.05):
        issues.append("disk-space")
    if not backup_fresh(backup_dir):
        issues.append("backup-age-or-authentication")
    backup_result = subprocess.run(["systemctl", "show", "bratstvo-backup.service", "-p", "Result", "--value"],
                                   capture_output=True, text=True, timeout=5)
    if backup_result.returncode or backup_result.stdout.strip() != "success":
        issues.append("backup-service")
    env = BASE_DIR / ".env"
    if env.is_symlink() or env.stat().st_uid != 0 or env.stat().st_mode & 0o027:
        issues.append("environment-permissions")
    if Path("/var/run/reboot-required").exists():
        issues.append("security-reboot-required")
    sync = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                          capture_output=True, text=True, timeout=5)
    if sync.returncode or sync.stdout.strip() != "yes":
        issues.append("clock-synchronization")
    return sorted(issues)


def record_state(directory, issues, now=None):
    now = time.time() if now is None else now
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink() or (os.name == "posix" and directory.stat().st_mode & 0o077):
        raise ValueError("Operational state directory must be private")
    target = directory / "status.json"
    previous = {}
    if target.exists():
        try:
            previous = json.loads(target.read_text())
        except (ValueError, OSError):
            pass
    consecutive = previous.get("consecutive_failures", 0) + 1 if issues else 0
    changed = previous.get("issues") != issues
    result = {"checked_at": now, "issues": issues, "consecutive_failures": consecutive,
              "last_healthy_at": previous.get("last_healthy_at") if issues else now}
    fd, name = tempfile.mkstemp(prefix=".status-", dir=directory)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(result, stream)
        os.replace(name, target)
    finally:
        Path(name).unlink(missing_ok=True)
    if changed or issues:
        print(json.dumps({"event": "operations-health", **result}))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=Path("/var/lib/bratstvo-monitor"))
    parser.add_argument("--backup-dir", type=Path, default=Path("/var/backups/bratstvo"))
    args = parser.parse_args()
    try:
        issues = collect(args.backup_dir)
    except Exception:
        issues = ["monitor-check-failed"]
    result = record_state(args.state_dir, issues)
    raise SystemExit(1 if result["issues"] else 0)
