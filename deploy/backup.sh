#!/usr/bin/env bash
# Зашифрованный бэкап базы и загруженных файлов. BACKUP_REMOTE_DIR должен
# указывать на отдельный смонтированный диск или удалённое хранилище.
set -euo pipefail

BACKUP_DIR=${BACKUP_DIR:-/var/backups/bratstvo}
BACKUP_KEY_FILE=${BACKUP_KEY_FILE:-/root/.bratstvo_backup_key}
BACKUP_REMOTE_DIR=${BACKUP_REMOTE_DIR:-}
KEEP_DAYS=${KEEP_DAYS:-14}
APP_DIR=${APP_DIR:-/opt/bratstvo}
STAMP=$(date -u +%Y-%m-%dT%H%M%S-%N)
CODE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)

if [[ ! -f "$BACKUP_KEY_FILE" ]]; then
    echo "Нет ключа шифрования $BACKUP_KEY_FILE; бэкап не создан" >&2
    exit 1
fi

# Read the same configuration as the services; never source .env as shell code.
# Keep the archive layout storage/... even when STORAGE_DIR has another name.
storage_dir=$(
    cd -- "$APP_DIR" || exit 1
    .venv/bin/python - "$BACKUP_DIR" "$BACKUP_REMOTE_DIR" <<'PY'
import sys
from pathlib import Path
from config import STORAGE_DIR

storage = STORAGE_DIR.resolve(strict=True)
if not storage.is_dir():
    raise SystemExit("STORAGE_DIR должен указывать на существующий каталог")
for destination in filter(None, sys.argv[1:]):
    path = Path(destination)
    if not path.is_absolute():
        raise SystemExit("Каталоги резервных копий должны иметь абсолютные пути")
    if path.resolve().is_relative_to(storage):
        raise SystemExit("Каталог резервных копий не может находиться внутри STORAGE_DIR")
print(storage)
PY
)

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
umask 077
work_dir=$(mktemp -d "$BACKUP_DIR/.work.XXXXXXXX")
trap 'rm -rf -- "$work_dir"' EXIT

sudo -u postgres pg_dump --format=custom bratstvo > "$work_dir/database.dump"
tar czf "$work_dir/storage.tar.gz" --transform='flags=rh;s,^\.,storage,' -C "$storage_dir" .
install -m 600 "$APP_DIR/.env" "$work_dir/application.env"

target="$BACKUP_DIR/bratstvo-$STAMP.tar.gz.enc"
tmp_target="$target.tmp"
tar czf - -C "$work_dir" database.dump storage.tar.gz application.env \
    | openssl enc -aes-256-cbc -pbkdf2 -salt -pass "file:$BACKUP_KEY_FILE" -out "$tmp_target"

# Проверяем расшифровку и структуру до публикации файла.
openssl enc -d -aes-256-cbc -pbkdf2 -pass "file:$BACKUP_KEY_FILE" -in "$tmp_target" \
    | tar tzf - >/dev/null
mv "$tmp_target" "$target"
chmod 600 "$target"
"$APP_DIR/.venv/bin/python" "$CODE_DIR/scripts/backup_integrity.py" sign "$target" --key-file "$BACKUP_KEY_FILE"
"$APP_DIR/.venv/bin/python" "$CODE_DIR/scripts/backup_integrity.py" verify "$target" --key-file "$BACKUP_KEY_FILE"

if [[ -n "$BACKUP_REMOTE_DIR" ]]; then
    if [[ ! -d "$BACKUP_REMOTE_DIR" ]]; then
        echo "BACKUP_REMOTE_DIR недоступен: $BACKUP_REMOTE_DIR" >&2
        exit 1
    fi
    install -m 600 "$target" "$BACKUP_REMOTE_DIR/$(basename "$target")"
    install -m 600 "$target.hmac" "$BACKUP_REMOTE_DIR/$(basename "$target").hmac"
    find "$BACKUP_REMOTE_DIR" -maxdepth 1 -type f -name 'bratstvo-*.tar.gz.enc' -mtime "+$KEEP_DAYS" -delete
    find "$BACKUP_REMOTE_DIR" -maxdepth 1 -type f -name 'bratstvo-*.tar.gz.enc.hmac' -mtime "+$KEEP_DAYS" -delete
fi

find "$BACKUP_DIR" -maxdepth 1 -type f -name 'bratstvo-*.tar.gz.enc' -mtime "+$KEEP_DAYS" -delete
find "$BACKUP_DIR" -maxdepth 1 -type f -name 'bratstvo-*.tar.gz.enc.hmac' -mtime "+$KEEP_DAYS" -delete
echo "Зашифрованный бэкап проверен: $target"
