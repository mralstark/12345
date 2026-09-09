#!/usr/bin/env bash
# Бэкап базы и загруженных документов. Ставится в cron на сервере:
#   (crontab -l 2>/dev/null; echo "30 3 * * * /opt/bratstvo/deploy/backup.sh") | crontab -
set -euo pipefail

BACKUP_DIR=${BACKUP_DIR:-/var/backups/bratstvo}
KEEP_DAYS=${KEEP_DAYS:-14}
STAMP=$(date +%Y-%m-%d)

# В копиях лежит весь состав: имена, телефоны, даты рождения, ники в телеграме,
# плюс загруженные фотографии. Раньше каталог и файлы создавались открытыми на
# чтение всем — то есть любая учётная запись на сервере, включая ту, под
# которой работает само приложение, могла забрать состав целиком одной
# командой. Смысл прав как раз в том, чтобы уцелеть, если приложение однажды
# взломают. Читать копии должен только root, он же их и делает.
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
umask 077

# Дамп базы: pg_dump ходит локально под ролью postgres, пароль не нужен.
sudo -u postgres pg_dump --format=custom bratstvo > "$BACKUP_DIR/db-$STAMP.dump"

# Документы (модуль «Документы» хранит файлы на диске, не в БД).
tar czf "$BACKUP_DIR/storage-$STAMP.tar.gz" -C /var/lib/bratstvo storage

# Чистка старого.
find "$BACKUP_DIR" -type f -mtime "+$KEEP_DAYS" -delete

# umask выше закрывает то, что создаём мы сами, но дамп базы пишет pg_dump
# через перенаправление от нашей же оболочки — на всякий случай выставляем
# права явно, чтобы не зависеть от того, кто именно создал файл.
chmod 600 "$BACKUP_DIR/db-$STAMP.dump" "$BACKUP_DIR/storage-$STAMP.tar.gz"

echo "Бэкап готов: $BACKUP_DIR/db-$STAMP.dump, $BACKUP_DIR/storage-$STAMP.tar.gz"
