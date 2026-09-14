#!/usr/bin/env bash
# Зашифрованный бэкап базы и загруженных файлов. BACKUP_REMOTE_DIR должен
# указывать на отдельный смонтированный диск или удалённое хранилище.
set -euo pipefail

BACKUP_DIR=${BACKUP_DIR:-/var/backups/bratstvo}
BACKUP_KEY_FILE=${BACKUP_KEY_FILE:-/root/.bratstvo_backup_key}
BACKUP_REMOTE_DIR=${BACKUP_REMOTE_DIR:-}
KEEP_DAYS=${KEEP_DAYS:-14}
STAMP=$(date +%Y-%m-%dT%H%M%S)

if [[ ! -f "$BACKUP_KEY_FILE" ]]; then
    echo "Нет ключа шифрования $BACKUP_KEY_FILE; бэкап не создан" >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
umask 077
work_dir=$(mktemp -d "$BACKUP_DIR/.work.XXXXXXXX")
trap 'rm -rf -- "$work_dir"' EXIT

sudo -u postgres pg_dump --format=custom bratstvo > "$work_dir/database.dump"
tar czf "$work_dir/storage.tar.gz" -C /var/lib/bratstvo storage

target="$BACKUP_DIR/bratstvo-$STAMP.tar.gz.enc"
tmp_target="$target.tmp"
tar czf - -C "$work_dir" database.dump storage.tar.gz \
    | openssl enc -aes-256-cbc -pbkdf2 -salt -pass "file:$BACKUP_KEY_FILE" -out "$tmp_target"

# Проверяем расшифровку и структуру до публикации файла.
openssl enc -d -aes-256-cbc -pbkdf2 -pass "file:$BACKUP_KEY_FILE" -in "$tmp_target" \
    | tar tzf - >/dev/null
mv "$tmp_target" "$target"
chmod 600 "$target"

if [[ -n "$BACKUP_REMOTE_DIR" ]]; then
    if [[ ! -d "$BACKUP_REMOTE_DIR" ]]; then
        echo "BACKUP_REMOTE_DIR недоступен: $BACKUP_REMOTE_DIR" >&2
        exit 1
    fi
    install -m 600 "$target" "$BACKUP_REMOTE_DIR/$(basename "$target")"
    find "$BACKUP_REMOTE_DIR" -maxdepth 1 -type f -name 'bratstvo-*.tar.gz.enc' -mtime "+$KEEP_DAYS" -delete
fi

find "$BACKUP_DIR" -maxdepth 1 -type f -name 'bratstvo-*.tar.gz.enc' -mtime "+$KEEP_DAYS" -delete
echo "Зашифрованный бэкап проверен: $target"
