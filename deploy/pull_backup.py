"""Copy and authenticate encrypted VPS backups to an owner-controlled computer.

No provider credentials or .env are downloaded. SSH key and recovery key are
provisioned separately; verify authentication before decrypting an archive.
"""
import argparse
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import time
import os

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.backup_integrity import verify  # noqa: E402

ARCHIVE = re.compile(r"bratstvo-\d{4}-\d{2}-\d{2}T\d{6}(?:-\d+)?\.tar\.gz\.enc")
REMOTE_DIR = "/var/backups/bratstvo/"


def pull(server, destination, key_file, identity):
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*@[A-Za-z0-9][A-Za-z0-9.-]*", server):
        raise ValueError("Invalid SSH destination")
    if destination.is_symlink():
        raise ValueError("Backup destination must not be a symbolic link")
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = destination.resolve()
    if os.name == "posix" and destination.stat().st_mode & 0o077:
        raise ValueError("Backup destination must be private")
    if not key_file.is_file():
        raise ValueError("A private recovery key is required")
    options = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-i", str(identity)]
    code = """import json,time
from pathlib import Path
files=[p for p in Path('/var/backups/bratstvo').glob('bratstvo-*.tar.gz.enc') if p.is_file() and not p.is_symlink() and Path(str(p)+'.hmac').is_file()]
latest=max(files,key=lambda p:p.stat().st_mtime)
print(json.dumps({'name':latest.name,'age':time.time()-latest.stat().st_mtime}))
"""
    result = subprocess.run(["ssh", *options, server, "python3 -c " + shlex.quote(code)],
                            capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError("Unable to locate an authenticated remote backup")
    info = json.loads(result.stdout)
    name = info["name"]
    if not ARCHIVE.fullmatch(name) or not 0 <= info["age"] <= 48 * 3600:
        raise ValueError("Remote backup is missing, stale or invalid")
    with tempfile.TemporaryDirectory(prefix=".download-", dir=destination) as temporary:
        stage = Path(temporary).resolve()
        if not stage.is_relative_to(destination):
            raise ValueError("Invalid temporary backup directory")
        for filename in (name, name + ".hmac"):
            result = subprocess.run(["scp", *options, server + ":" + REMOTE_DIR + filename, str(stage / filename)],
                                    capture_output=True, timeout=180)
            if result.returncode:
                raise RuntimeError("Encrypted backup transfer failed")
        verify(stage / name, key_file)
        for filename in (name, name + ".hmac"):
            os.replace(stage / filename, destination / filename)
    # Only exact backup names inside this explicit directory are eligible.
    for old in destination.iterdir():
        base = old.name.removesuffix(".hmac")
        if (ARCHIVE.fullmatch(base) and not old.is_symlink() and old.is_file()
                and old.resolve().is_relative_to(destination) and time.time() - old.stat().st_mtime > 14 * 86400):
            old.unlink()
    status = {"backup": name, "authenticated": True, "checked_at": time.time()}
    fd, temporary = tempfile.mkstemp(prefix=".last-pull-", dir=destination)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(status, stream)
        os.replace(temporary, destination / "last-pull.json")
    finally:
        Path(temporary).unlink(missing_ok=True)
    if sys.stdout is not None:
        print(json.dumps(status))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", required=True)
    parser.add_argument("--destination", type=Path, default=Path.home() / ".bratstvo-backups")
    parser.add_argument("--key-file", type=Path, default=Path.home() / ".ssh" / "bratstvo-backup-recovery.key")
    parser.add_argument("--identity", type=Path, default=Path.home() / ".ssh" / "beget_bratstvo_ed25519")
    args = parser.parse_args()
    try:
        pull(args.server, args.destination, args.key_file, args.identity)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
        raise SystemExit("Backup pull failed; check transport, backup freshness and the local recovery key") from None
