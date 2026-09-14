#!/usr/bin/env bash
# Заливка кода на сервер с macOS/Linux.
#
# Первый раз (полная настройка сервера — пакеты, БД, systemd, nginx, HTTPS):
#   ./deploy/deploy.sh -s user@1.2.3.4 --bootstrap --email you@example.com
#
# Дальше, после любой правки кода:
#   ./deploy/deploy.sh -s user@1.2.3.4
#
# Посмотреть, что изменится на сервере, ничего не меняя:
#   ./deploy/deploy.sh -s user@1.2.3.4 --dry-run
#
# Логин не root (обычный VPS с sudo) — команды сами обернутся в sudo.
#
# Синхронизируем rsync'ом, а не распаковкой архива поверх: архив умеет только
# добавлять, поэтому файл, удалённый из репозитория, оставался жить на сервере
# вечно. rsync с --delete приводит каталог к тому, что в репозитории.
#
# Что на сервере своё и НЕ трогается (список --exclude ниже):
#   .env      — токен бота и пароль базы; локальный .env с прокси и туннелем
#               сервер сломает, поэтому серверный живёт своей жизнью;
#   .venv     — виртуальное окружение, ставится на сервере;
#   backups   — дампы базы перед миграциями;
#   storage   — загруженные фотографии (на этом сервере вынесены в
#               /var/lib/bratstvo/storage, но на дефолтной настройке лежат тут).
# Важно: именно --exclude, без --delete-excluded. Исключённое rsync не шлёт и
# не удаляет — а --delete-excluded снёс бы всё это подчистую.
set -euo pipefail

PORT=22
APP_DIR=/opt/bratstvo
BOOTSTRAP=false
DRY_RUN=false
EMAIL=""
DOMAIN=""
SERVER=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    -s|--server) SERVER="$2"; shift 2 ;;
    -p|--port) PORT="$2"; shift 2 ;;
    --bootstrap) BOOTSTRAP=true; shift ;;
    --dry-run) DRY_RUN=true; shift ;;
    --email) EMAIL="$2"; shift 2 ;;
    --domain) DOMAIN="$2"; shift 2 ;;
    --app-dir) APP_DIR="$2"; shift 2 ;;
    *) echo "Неизвестный аргумент: $1"; exit 1 ;;
  esac
done

if [[ -z "$SERVER" ]]; then
  echo "Использование: deploy.sh -s user@host [--bootstrap --email you@example.com] [--domain example.com] [-p port] [--app-dir /opt/bratstvo] [--dry-run]"
  exit 1
fi
if $BOOTSTRAP && [[ -z "$EMAIL" ]]; then
  echo "--bootstrap требует --email: на этот адрес Let's Encrypt пришлёт предупреждение о непродлённом сертификате."
  exit 1
fi

# На Google Cloud под root не заходят — там обычный пользователь с passwordless sudo.
REMOTE_PREFIX="sudo "
# Под обычным пользователем rsync на той стороне тоже должен запуститься через
# sudo, иначе ему не записать в /opt. Массив, а не строка: строку с пробелом
# оболочка разбила бы на два аргумента.
RSYNC_SUDO=(--rsync-path="sudo rsync")
if [[ "$SERVER" == root@* ]]; then
  REMOTE_PREFIX=""
  RSYNC_SUDO=()
fi

# Связь с этим сервером иногда рвётся посреди деплоя («Connection closed by
# …»). Скрипт при этом честно падал — но файлы уже уехали, а службы ещё не
# перезапустились, и сервер оставался наполовину обновлённым: на диске новый
# код, в памяти старый. Заметить это можно было только сверив время запуска
# службы. Поэтому каждый шаг пробуем трижды.
remote() {
  local attempt
  for attempt in 1 2 3; do
    if ssh -o ConnectTimeout=10 -p "$PORT" "$SERVER" "$REMOTE_PREFIX bash -c '$1'"; then
      return 0
    fi
    if [[ $attempt -lt 3 ]]; then
      echo "    связь оборвалась, повтор $((attempt + 1)) из 3…" >&2
      sleep 3
    fi
  done
  echo "Сервер не отвечает: шаг не выполнен. На сервере могли остаться новые файлы" >&2
  echo "со старыми запущенными службами — повторите деплой." >&2
  return 1
}

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

command -v rsync >/dev/null || { echo "На этой машине нет rsync — без него заливать нечем."; exit 1; }

# Своё на сервере — не шлём и не удаляем. Каждая строка объяснена в шапке файла.
KEEP=(--exclude=/.env --exclude=/.venv/ --exclude=/backups/ --exclude=/storage/)
# Служебное и локальное — на сервере ему делать нечего.
SKIP=(--exclude=/.git/ --exclude=/.claude/ --exclude=/.pytest_cache/
      --exclude=__pycache__/ --exclude='*.pyc' --exclude='*.db'
      --exclude=.DS_Store --exclude='/deploy/*.tar.gz')
# Макеты интерфейса и разбор лендинга — это рабочие материалы на несколько
# мегабайт, серверу они не нужны. Раз исключены, rsync их и не удалит —
# поэтому те, что уже уехали туда прошлыми деплоями, снимаем отдельно ниже.
DESIGN=(--exclude='/design-*/' --exclude=/landing-review/ --exclude=/scratch_deps.json)

remote "mkdir -p $APP_DIR"

# Обрыв связи посреди синхронизации оставлял часть файлов новыми, часть — нет,
# поэтому и здесь три попытки.
sync_files() {
  local attempt
  for attempt in 1 2 3; do
    if rsync -rlpt --delete "$@" \
        -e "ssh -o ConnectTimeout=10 -p $PORT" ${RSYNC_SUDO[@]+"${RSYNC_SUDO[@]}"} \
        "${KEEP[@]}" "${SKIP[@]}" "${DESIGN[@]}" \
        "$PROJECT_ROOT/" "${SERVER}:${APP_DIR}/"; then
      return 0
    fi
    if [[ $attempt -lt 3 ]]; then
      echo "    связь оборвалась, повтор $((attempt + 1)) из 3…" >&2
      sleep 3
    fi
  done
  echo "Синхронизация не удалась после трёх попыток." >&2
  return 1
}

if $DRY_RUN; then
  echo "==> ПРОВЕРКА: что rsync сделал бы на сервере (ничего не меняется)"
  sync_files --dry-run --itemize-changes
  echo "Проверка окончена, на сервере ничего не тронуто."
  exit 0
fi

echo "==> Синхронизация кода"
sync_files

echo "==> Описания служб"
# Systemd читает не то, что лежит в каталоге проекта, а свою копию в
# /etc/systemd/system. Без этого шага правка описания службы молча не
# доезжала: деплой говорил «Готово», а служба работала по-старому.
remote "APP_DIR=$APP_DIR bash $APP_DIR/deploy/sync_units.sh"

echo "==> Уборка того, что осталось от прежних деплоев"
# Раздельно от rsync: исключённое он не удаляет. Пути перечислены поимённо,
# чтобы никакой промах в переменной не превратил уборку в снос каталога.
remote "cd $APP_DIR && rm -rf ./design-* ./landing-review ./scratch_deps.json ./bratstvo.db && find . -path ./.venv -prune -o -name __pycache__ -type d -exec rm -rf {} +"

if $BOOTSTRAP; then
  echo "==> Первичная настройка сервера (пакеты, PostgreSQL, systemd, nginx, HTTPS)"
  ARGS="--email $EMAIL"
  [[ -n "$DOMAIN" ]] && ARGS="$ARGS --domain $DOMAIN"
  remote "bash $APP_DIR/deploy/setup_server.sh $ARGS"
else
  echo "==> Обновление зависимостей и перезапуск"
  remote "chown -R bratstvo:bratstvo $APP_DIR; if test -f $APP_DIR/requirements.lock; then sudo -u bratstvo $APP_DIR/.venv/bin/pip install --quiet --require-hashes -r $APP_DIR/requirements.lock; else sudo -u bratstvo $APP_DIR/.venv/bin/pip install --quiet -r $APP_DIR/requirements.txt; fi; cd $APP_DIR; sudo -u bratstvo $APP_DIR/.venv/bin/python -m scripts.migrate_security_constraints; systemctl restart bratstvo-api bratstvo-bot; sleep 3; systemctl is-active bratstvo-api bratstvo-bot"
fi

echo "Готово."
