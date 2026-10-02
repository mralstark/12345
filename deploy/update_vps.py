"""Обновление существующего Beget VPS с Windows, с бэкапом и возвратом кода.

python deploy/update_vps.py root@31.129.98.217
.env, база, ключи и загруженные файлы никогда не упаковываются.
В Git-копии отправляются только отслеживаемые файлы.
"""
import hashlib
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parent.parent
EXCLUDED_DIRS = {".git", ".venv", "storage", "backups", "__pycache__", ".pytest_cache", ".claude", "work"}


def include(path: Path) -> bool:
    return not (any(part in EXCLUDED_DIRS for part in path.relative_to(ROOT).parts)
                or (path.name.startswith(".env") and path.name != ".env.example")
                or path.suffix.lower() in {".pyc", ".db", ".pem", ".key", ".p12"})


def source_files():
    if (ROOT / ".git").exists():
        result = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True)
        return [ROOT / name for name in result.stdout.decode("utf-8").split("\0") if name]
    return ROOT.rglob("*")


def main():
    import sys
    if len(sys.argv) != 2 or not re.fullmatch(r"root@[A-Za-z0-9][A-Za-z0-9.-]*", sys.argv[1]):
        raise SystemExit("Usage: python deploy/update_vps.py root@SERVER_IP")
    server = sys.argv[1]
    with tempfile.TemporaryDirectory(prefix="bratstvo-update-") as temporary:
        archive = Path(temporary) / "code.tar.gz"
        with tarfile.open(archive, "w:gz") as output:
            for path in source_files():
                if path.is_file() and include(path):
                    output.add(path, arcname=path.relative_to(ROOT).as_posix())
        checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
        remote = f"/root/.cache/bratstvo-deploy/code-{checksum[:16]}.tar.gz"
        subprocess.run(["ssh", server, "umask 077; mkdir -p /root/.cache/bratstvo-deploy; chmod 700 /root/.cache/bratstvo-deploy"], check=True)
        subprocess.run(["scp", str(archive), f"{server}:{remote}"], check=True)
        script = r'''set -euo pipefail
exec 9>/run/lock/bratstvo-deploy.lock
flock -n 9 || { echo 'Another deployment is running'; exit 1; }
ARCHIVE=__ARCHIVE__
echo '__CHECKSUM__  '__ARCHIVE__ | sha256sum --check --status
systemctl start bratstvo-backup.service
STAMP=$(date -u +%Y%m%dT%H%M%S)
mkdir -p /opt/bratstvo-releases
chmod 700 /opt/bratstvo-releases
ROLLBACK=/opt/bratstvo-releases/code-$STAMP.tar.gz
tar --exclude=.venv --exclude=.env --exclude=storage --exclude=backups --exclude=.git --exclude='*.db' -czf "$ROLLBACK" -C /opt/bratstvo .
# Зависимости устанавливаются заранее; пока сервисы работают на прежнем коде.
STAGING=$(mktemp -d /tmp/bratstvo-stage.XXXXXXXX)
tar xzf "$ARCHIVE" -C "$STAGING"
/opt/bratstvo/.venv/bin/pip install --quiet --require-hashes -r "$STAGING/requirements.lock"
rollback() {
  code=$?
  trap - ERR
  echo 'Update failed; restoring previous application code'
  tar xzf "$ROLLBACK" -C /opt/bratstvo
  chown -R bratstvo:bratstvo /opt/bratstvo
  chown root:bratstvo /opt/bratstvo/.env
  chmod 640 /opt/bratstvo/.env
  systemctl start bratstvo-api.socket bratstvo-api bratstvo-bot
  exit "$code"
}
trap rollback ERR
systemctl stop bratstvo-api bratstvo-api.socket bratstvo-bot
tar xzf "$ARCHIVE" -C /opt/bratstvo
chown -R bratstvo:bratstvo /opt/bratstvo
chown root:bratstvo /opt/bratstvo/.env
chmod 640 /opt/bratstvo/.env
bash /opt/bratstvo/deploy/sync_units.sh
cd /opt/bratstvo
for migration in migrate_security_constraints migrate_university_active migrate_operational_roles_and_reviews migrate_cabinet_revisions migrate_gamification; do
  sudo -u bratstvo /opt/bratstvo/.venv/bin/python -m scripts.$migration
done
systemctl start bratstvo-api.socket bratstvo-api bratstvo-bot
sleep 3
systemctl is-active --quiet bratstvo-api bratstvo-bot
curl --fail --silent --output /dev/null http://127.0.0.1:8000/
trap - ERR
rm -f "$ARCHIVE"
# Staging is a mktemp directory created above, outside application data.
rm -rf -- "$STAGING"
echo "Update completed; previous code: $ROLLBACK"
'''.replace("__ARCHIVE__", remote).replace("__CHECKSUM__", checksum)
        subprocess.run(["ssh", server, "bash -s"], input=script.encode("utf-8"), check=True)


if __name__ == "__main__":
    main()
