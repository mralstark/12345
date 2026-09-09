#!/usr/bin/env bash
# Доставляет описания служб из каталога проекта в систему. Запускается на
# сервере из deploy.sh, сразу после заливки кода и до перезапуска.
#
# Зачем: описание службы существует в двух копиях — в проекте
# (/opt/bratstvo/deploy) и в системе (/etc/systemd/system). Systemd читает
# только вторую. Обычный деплой обновлял первую и не трогал вторую, поэтому
# правка предела памяти или строки запуска молча не доезжала: деплой говорил
# «Готово», служба перезапускалась и работала по-старому.
#
# Настройку nginx здесь НЕ трогаем, и это важно. После первичной установки её
# правит certbot — дописывает сертификаты и перенаправление на HTTPS. Шаблон в
# проекте про них ничего не знает, и перезапись шаблоном снесла бы HTTPS.
# Поэтому про nginx только предупреждаем, а применяет человек руками.
set -euo pipefail

APP_DIR=${APP_DIR:-/opt/bratstvo}
ENV_FILE="$APP_DIR/.env"

value_from_env() {
    [ -f "$ENV_FILE" ] || return 0
    sed -n "s|^$1=||p" "$ENV_FILE" | tail -1
}

# Порт и домен берём с самого сервера: deploy.sh их не знает, а .env — знает.
API_BASE_URL=$(value_from_env API_BASE_URL)
WEBAPP_URL=$(value_from_env WEBAPP_URL)
PORT=$(printf '%s' "$API_BASE_URL" | sed -n 's|.*:\([0-9]\{2,5\}\)/*$|\1|p')
DOMAIN=$(printf '%s' "$WEBAPP_URL" | sed -e 's|^https\{0,1\}://||' -e 's|/.*$||')
PORT=${PORT:-8000}

units_changed=0
socket_changed=0

install_if_changed() {  # <файл в проекте> <путь в системе> <что это>
    local src="$1" dst="$2" label="$3" tmp
    [ -f "$src" ] || return 0
    tmp=$(mktemp)
    sed -e "s|__PORT__|$PORT|g" -e "s|__DOMAIN__|$DOMAIN|g" "$src" > "$tmp"
    if [ -f "$dst" ] && cmp -s "$tmp" "$dst"; then
        rm -f "$tmp"
        return 0
    fi
    install -m 644 "$tmp" "$dst"
    rm -f "$tmp"
    echo "    обновлено описание: $label"
    units_changed=1
    [ "$label" = "bratstvo-api.socket" ] && socket_changed=1
    return 0
}

install_if_changed "$APP_DIR/deploy/bratstvo-api.service" /etc/systemd/system/bratstvo-api.service bratstvo-api.service
install_if_changed "$APP_DIR/deploy/bratstvo-api.socket"  /etc/systemd/system/bratstvo-api.socket  bratstvo-api.socket
install_if_changed "$APP_DIR/deploy/bratstvo-bot.service" /etc/systemd/system/bratstvo-bot.service bratstvo-bot.service

if [ "$units_changed" = "1" ]; then
    systemctl daemon-reload
    # Сокет перезапускаем только если поменялся он сам: это единственный
    # момент, когда порт всё-таки закрывается на мгновение.
    if [ "$socket_changed" = "1" ]; then
        echo "    перезапускаю порт (описание сокета изменилось)"
        systemctl restart bratstvo-api.socket
    fi
fi

# --- nginx: только предупреждение ---------------------------------------------
# Сверяем не с файлом в системе (его правил certbot, разойдётся всегда), а с
# отпечатком шаблона на момент последней установки.
NGINX_TEMPLATE="$APP_DIR/deploy/nginx-bratstvo.conf"
NGINX_STAMP=/etc/nginx/.bratstvo-template.sha256

if [ -f "$NGINX_TEMPLATE" ]; then
    current=$(sha256sum "$NGINX_TEMPLATE" | cut -d' ' -f1)
    if [ ! -f "$NGINX_STAMP" ]; then
        # Первый запуск на уже настроенном сервере: считаем нынешний шаблон
        # применённым, иначе предупреждали бы на ровном месте.
        printf '%s\n' "$current" > "$NGINX_STAMP"
    elif [ "$current" != "$(cat "$NGINX_STAMP")" ]; then
        echo
        echo "    ВНИМАНИЕ: настройка nginx в проекте изменилась, но применена НЕ будет."
        echo "    В системе её правил certbot (сертификаты, перенаправление на HTTPS),"
        echo "    и перезапись шаблоном снесла бы HTTPS. Перенесите правку руками:"
        echo "      /etc/nginx/sites-available/bratstvo"
        echo "    затем: nginx -t && systemctl reload nginx"
        echo "    После этого отметьте применённой:"
        echo "      sha256sum $NGINX_TEMPLATE | cut -d' ' -f1 > $NGINX_STAMP"
        echo
    fi
fi

[ "$units_changed" = "1" ] || echo "    описания служб не менялись"
