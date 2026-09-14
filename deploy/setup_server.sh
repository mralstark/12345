#!/usr/bin/env bash
# Первичная настройка чистого Ubuntu-сервера под личный кабинет Братства.
# Запускается на сервере от root ПОСЛЕ того, как код залит в /opt/bratstvo
# (это делает deploy/deploy.ps1 с ключом -Bootstrap).
#
#   bash /opt/bratstvo/deploy/setup_server.sh --email you@example.com [--domain lk.example.com]
#
# Без --domain адрес берётся автоматически как <IP-через-дефисы>.sslip.io —
# отдельный домен покупать не нужно, Let's Encrypt такой сертификат выдаёт.
# Скрипт идемпотентен: повторный запуск ничего не ломает.
set -euo pipefail

APP_USER=bratstvo
APP_DIR=/opt/bratstvo
DATA_DIR=/var/lib/bratstvo
VENV="$APP_DIR/.venv"
DOMAIN=""
EMAIL=""
PORT=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --domain) DOMAIN="$2"; shift 2 ;;
        --email)  EMAIL="$2";  shift 2 ;;
        --port)   PORT="$2";   shift 2 ;;
        *) echo "Неизвестный аргумент: $1" >&2; exit 1 ;;
    esac
done

if [[ $EUID -ne 0 ]]; then
    echo "Запускать от root: sudo bash $0 ..." >&2
    exit 1
fi

if [[ -z "$EMAIL" ]]; then
    echo "Нужен --email — на него Let's Encrypt пришлёт предупреждение, если сертификат перестанет продлеваться." >&2
    exit 1
fi

if [[ -z "$DOMAIN" ]]; then
    IP=$(curl -fsS --max-time 10 https://api.ipify.org || hostname -I | awk '{print $1}')
    # Префикс lk. — чтобы не столкнуться с чужим vhost'ом, если сервер уже кем-то занят:
    # sslip.io резолвит любой поддомен вида что-угодно.1-2-3-4.sslip.io в тот же IP.
    DOMAIN="lk.${IP//./-}.sslip.io"
    echo "Домен не задан — использую $DOMAIN (sslip.io резолвится в $IP)."
fi

# Сервер может быть общим с другими проектами — занимать чужой порт нельзя.
if [[ -z "$PORT" ]]; then
    PORT=8000
    while ss -ltn "sport = :$PORT" 2>/dev/null | grep -q LISTEN; do
        PORT=$((PORT + 1))
        [[ $PORT -gt 8020 ]] && { echo "Не нашёл свободный порт в диапазоне 8000-8020" >&2; exit 1; }
    done
fi
echo "Порт бэкенда: $PORT"

PY_OK=$(python3 -c 'import sys; print(1 if sys.version_info >= (3, 11) else 0)' 2>/dev/null || echo 0)
if [[ "$PY_OK" != "1" ]]; then
    echo "Нужен Python 3.11+. Текущий: $(python3 --version 2>&1). На Debian 11/Ubuntu 20.04" >&2
    echo "поставьте новее (deadsnakes PPA или обновите образ) и запустите скрипт снова." >&2
    exit 1
fi

echo "==> Пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip postgresql nginx certbot \
    python3-certbot-nginx fonts-dejavu-core curl ca-certificates openssl

echo "==> Своп"
# На машинах с 1 ГБ памяти (например, GCP e2-micro) своп страхует от OOM —
# особенно если на сервере живёт ещё чей-то процесс.
RAM_MB=$(free -m | awk '/^Mem:/{print $2}')
SWAP_MB=$(free -m | awk '/^Swap:/{print $2}')
if [[ "$RAM_MB" -lt 2048 && "$SWAP_MB" -lt 512 ]]; then
    if [[ ! -f /swapfile ]]; then
        fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
        chmod 600 /swapfile
        mkswap /swapfile
        swapon /swapfile
        grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
        echo "    создан /swapfile на 2 ГБ (RAM: ${RAM_MB} МБ)"
    fi
else
    echo "    не требуется (RAM: ${RAM_MB} МБ, своп: ${SWAP_MB} МБ)"
fi

echo "==> Системный пользователь и каталоги"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
mkdir -p "$APP_DIR" "$DATA_DIR/storage"
chown -R "$APP_USER:$APP_USER" "$APP_DIR" "$DATA_DIR"

echo "==> Виртуальное окружение и зависимости"
[[ -d "$VENV" ]] || python3 -m venv "$VENV"
chown -R "$APP_USER:$APP_USER" "$VENV"
sudo -u "$APP_USER" "$VENV/bin/pip" install --quiet --upgrade pip
REQUIREMENTS="$APP_DIR/requirements.lock"
[[ -f "$REQUIREMENTS" ]] || REQUIREMENTS="$APP_DIR/requirements.txt"
# На ARM (Oracle Ampere) готовое колесо есть не для каждой версии пакета —
# тогда его надо собрать, а для этого нужен компилятор.
PIP_HASH_ARGS=()
[[ "$REQUIREMENTS" == *.lock ]] && PIP_HASH_ARGS=(--require-hashes)
if ! sudo -u "$APP_USER" "$VENV/bin/pip" install --quiet "${PIP_HASH_ARGS[@]}" -r "$REQUIREMENTS"; then
    echo "    не хватило готовых пакетов ($(uname -m)) — ставлю инструменты сборки"
    apt-get install -y -qq build-essential python3-dev libffi-dev
    sudo -u "$APP_USER" "$VENV/bin/pip" install "${PIP_HASH_ARGS[@]}" -r "$REQUIREMENTS"
fi

echo "==> PostgreSQL"
systemctl enable --now postgresql
# Машина может быть маленькой и не только нашей — держим Postgres компактным.
# Настройки по умолчанию рассчитаны на сервер побольше, чем 1 ГБ на всех.
PG_CONF_DIR=$(ls -d /etc/postgresql/*/main/conf.d 2>/dev/null | head -1 || true)
if [[ -n "$PG_CONF_DIR" && "$RAM_MB" -lt 2048 ]]; then
    cat > "$PG_CONF_DIR/10-bratstvo-small.conf" <<'PGCONF'
shared_buffers = 64MB
effective_cache_size = 256MB
max_connections = 20
work_mem = 2MB
maintenance_work_mem = 32MB
PGCONF
    systemctl restart postgresql
    echo "    применены компактные настройки (RAM: ${RAM_MB} МБ)"
fi
BACKUP_KEY_FILE=/root/.bratstvo_backup_key
if [[ ! -f "$BACKUP_KEY_FILE" ]]; then
    openssl rand -hex 32 > "$BACKUP_KEY_FILE"
    chmod 600 "$BACKUP_KEY_FILE"
    echo "    создан отдельный ключ шифрования резервных копий"
fi

DB_PASS_FILE=/root/.bratstvo_db_password
if [[ -f "$DB_PASS_FILE" ]]; then
    DB_PASS=$(cat "$DB_PASS_FILE")
else
    DB_PASS=$(head -c 24 /dev/urandom | base64 | tr -d '/+=' | head -c 24)
    printf '%s' "$DB_PASS" > "$DB_PASS_FILE"
    chmod 600 "$DB_PASS_FILE"
fi
sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='bratstvo'" | grep -q 1 \
    || sudo -u postgres psql -qc "CREATE ROLE bratstvo LOGIN PASSWORD '$DB_PASS'"
sudo -u postgres psql -qc "ALTER ROLE bratstvo PASSWORD '$DB_PASS'"
sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='bratstvo'" | grep -q 1 \
    || sudo -u postgres createdb -O bratstvo bratstvo

MEDIA_KEY_FILE=/root/.bratstvo_media_signing_key
if [[ -f "$MEDIA_KEY_FILE" ]]; then
    MEDIA_SIGNING_KEY=$(cat "$MEDIA_KEY_FILE")
else
    MEDIA_SIGNING_KEY=$(head -c 32 /dev/urandom | base64 | tr -d '\n')
    printf '%s' "$MEDIA_SIGNING_KEY" > "$MEDIA_KEY_FILE"
    chmod 600 "$MEDIA_KEY_FILE"
fi

echo "==> .env"
# Токен и superuser'ы дописываются руками — здесь только то, что знает сервер.
if [[ ! -f "$APP_DIR/.env" ]]; then
    cat > "$APP_DIR/.env" <<EOF
# Заполните BOT_TOKEN и SUPERUSER_TELEGRAM_IDS, потом:
#   systemctl restart bratstvo-bot bratstvo-api
BOT_TOKEN=
SUPERUSER_TELEGRAM_IDS=
AUTO_FEDERAL_TELEGRAM_IDS=
MEDIA_SIGNING_KEY=$MEDIA_SIGNING_KEY
APP_ENV=production

# На сервере вне РФ прокси не нужен — оставить пустым.
BOT_PROXY_URL=

WEBAPP_URL=https://$DOMAIN
ALLOWED_HOSTS=$DOMAIN
CORS_ALLOWED_ORIGINS=
DATABASE_URL=postgresql+asyncpg://bratstvo:$DB_PASS@localhost:5432/bratstvo
APP_TIMEZONE=Europe/Moscow
STORAGE_DIR=$DATA_DIR/storage
API_BASE_URL=http://127.0.0.1:$PORT
REPORT_FONT_PATH=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf

# Обязано быть пустым: непустое значение отключает проверку подписи Telegram.
DEV_TELEGRAM_ID=
EOF
    echo "    создан $APP_DIR/.env — впишите BOT_TOKEN"
else
    # Пароль БД и адрес могли поменяться (пересоздание роли, новый домен).
    sed -i "s|^DATABASE_URL=.*|DATABASE_URL=postgresql+asyncpg://bratstvo:$DB_PASS@localhost:5432/bratstvo|" "$APP_DIR/.env"
    sed -i "s|^WEBAPP_URL=.*|WEBAPP_URL=https://$DOMAIN|" "$APP_DIR/.env"
    sed -i "s|^API_BASE_URL=.*|API_BASE_URL=http://127.0.0.1:$PORT|" "$APP_DIR/.env"
    set_env() {
        local key="$1" value="$2"
        if grep -q "^${key}=" "$APP_DIR/.env"; then
            sed -i "s|^${key}=.*|${key}=${value}|" "$APP_DIR/.env"
        else
            printf '\n%s=%s\n' "$key" "$value" >> "$APP_DIR/.env"
        fi
    }
    set_env APP_ENV production
    set_env MEDIA_SIGNING_KEY "$MEDIA_SIGNING_KEY"
    set_env ALLOWED_HOSTS "$DOMAIN"
    grep -q '^AUTO_FEDERAL_TELEGRAM_IDS=' "$APP_DIR/.env" || printf '\nAUTO_FEDERAL_TELEGRAM_IDS=\n' >> "$APP_DIR/.env"
    if grep -q '^AUTO_FEDERAL_FULL_NAMES=.' "$APP_DIR/.env"; then
        sed -i '/^AUTO_FEDERAL_FULL_NAMES=/d' "$APP_DIR/.env"
        echo "    удалён небезопасный AUTO_FEDERAL_FULL_NAMES — перенесите доверенных людей по Telegram ID"
    fi
    echo "    $APP_DIR/.env уже был — обновил DATABASE_URL, WEBAPP_URL и API_BASE_URL"
fi
chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
chmod 600 "$APP_DIR/.env"

echo "==> Миграции безопасности"
(cd "$APP_DIR" && sudo -u "$APP_USER" "$VENV/bin/python" -m scripts.migrate_security_constraints)

echo "==> systemd"
install -m 644 "$APP_DIR/deploy/bratstvo-bot.service" /etc/systemd/system/bratstvo-bot.service
sed "s|__PORT__|$PORT|g" "$APP_DIR/deploy/bratstvo-api.service" > /etc/systemd/system/bratstvo-api.service
chmod 644 /etc/systemd/system/bratstvo-api.service
# Порт держит отдельный сокет-юнит, чтобы перезапуск не рвал соединения
# (объяснение — в самом файле bratstvo-api.socket).
sed "s|__PORT__|$PORT|g" "$APP_DIR/deploy/bratstvo-api.socket" > /etc/systemd/system/bratstvo-api.socket
chmod 644 /etc/systemd/system/bratstvo-api.socket
systemctl daemon-reload
systemctl enable bratstvo-bot bratstvo-api bratstvo-api.socket

echo "==> nginx"
# Свой vhost по server_name. Чужие сайты на этом сервере не трогаем — в том числе
# default: он может обслуживать соседний проект.
sed -e "s|__DOMAIN__|$DOMAIN|g" -e "s|__PORT__|$PORT|g" \
    "$APP_DIR/deploy/nginx-bratstvo.conf" > /etc/nginx/sites-available/bratstvo
ln -sf /etc/nginx/sites-available/bratstvo /etc/nginx/sites-enabled/bratstvo
nginx -t
systemctl reload nginx
sha256sum "$APP_DIR/deploy/nginx-bratstvo.conf" | cut -d' ' -f1 > /etc/nginx/.bratstvo-template.sha256

echo "==> Локальный файрвол"
# У образов Oracle Linux/Ubuntu на Oracle Cloud, кроме облачных Security List,
# есть ещё и локальные правила iptables, где всё кроме 22 порта отбрасывается.
# Без этого шага certbot не подтвердит домен, даже если в облаке порты открыты.
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw allow 80/tcp >/dev/null
    ufw allow 443/tcp >/dev/null
    echo "    ufw: открыты 80 и 443"
elif command -v iptables >/dev/null && iptables -L INPUT -n 2>/dev/null | grep -qE 'REJECT|DROP'; then
    for p in 80 443; do
        iptables -C INPUT -p tcp --dport "$p" -j ACCEPT 2>/dev/null \
            || iptables -I INPUT 1 -p tcp --dport "$p" -j ACCEPT
    done
    if command -v netfilter-persistent >/dev/null; then
        netfilter-persistent save >/dev/null
        echo "    iptables: открыты 80 и 443, правила сохранены"
    else
        echo "    iptables: открыты 80 и 443 (без netfilter-persistent — после перезагрузки проверьте)"
    fi
else
    echo "    не требуется"
fi

echo "==> HTTPS (Let's Encrypt)"
# Telegram открывает Mini App только по HTTPS с валидным сертификатом.
if [[ ! -d "/etc/letsencrypt/live/$DOMAIN" ]]; then
    if ! certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL" --redirect; then
        cat >&2 <<'HINT'

Certbot не смог подтвердить домен. Самая частая причина — закрытые порты
80 и 443 на уровне облака (у некоторых провайдеров это отдельный файрфол/
Security Group поверх ОС, локальный iptables этого скрипта его не видит
и не может открыть сам): проверьте в панели хостера настройки сети/
файрвола для этого сервера и разрешите входящие TCP 80 и 443 отовсюду.

После этого запустите скрипт ещё раз — он продолжит с этого места.
HINT
        exit 1
    fi
else
    echo "    сертификат для $DOMAIN уже есть"
fi
systemctl enable --now certbot.timer 2>/dev/null || true

echo "==> Запуск сервисов"
systemctl restart bratstvo-api
systemctl restart bratstvo-bot
sleep 3
systemctl is-active --quiet bratstvo-api && echo "    api: работает" || echo "    api: НЕ работает — journalctl -u bratstvo-api -n 50"
systemctl is-active --quiet bratstvo-bot && echo "    bot: работает" || echo "    bot: НЕ работает (обычно пустой BOT_TOKEN) — journalctl -u bratstvo-bot -n 50"

echo
echo "Готово. Адрес кабинета: https://$DOMAIN"
echo "Дальше:"
echo "  1. nano $APP_DIR/.env  — вписать BOT_TOKEN и свой telegram_id в SUPERUSER_TELEGRAM_IDS"
echo "  2. systemctl restart bratstvo-bot bratstvo-api"
echo "  3. curl -s https://$DOMAIN/healthz   → {\"status\":\"ok\"}"
