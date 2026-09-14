# Деплой на сервер

Пока проект живёт на домашнем компьютере, у него две болячки: Telegram доступен
только через локальный прокси, а Mini App требует публичного HTTPS — туннель даёт
его на время и с новым адресом при каждом перезапуске. Сервер снимает обе: бот и
кабинет работают круглосуточно, адрес постоянный, прокси не нужен.

Всё, что нужно от вас — сервер и SSH-доступ к нему. Остальное делают скрипты
из `deploy/`. Разворачиваем на существующую ВМ Google Cloud (раздел 0); если
когда-нибудь понадобится отдельная машина — раздел 1.

---

## 0. Развёртывание на существующую ВМ Google Cloud

Текущий план: ставим на уже имеющуюся `e2-micro` (2 vCPU, 1 ГБ), где живёт другой
бот. Скрипты рассчитаны на соседство и чужого не трогают:

- отдельный системный пользователь `bratstvo` и каталог `/opt/bratstvo`;
- своя база в PostgreSQL, свой `.env`;
- порт бэкенда подбирается свободный (8000, если занят — 8001 и дальше);
- свой nginx-vhost по имени `lk.<ip-через-дефисы>.sslip.io`; чужие сайты,
  включая `default`, остаются как есть;
- `MemoryMax=350M` на каждый наш сервис — при нехватке памяти systemd остановит
  наш процесс, а не соседский бот;
- своп 2 ГБ, если его ещё нет (на 1 ГБ памяти это страховка от OOM).

Что нужно сделать в консоли Google перед деплоем:

1. **Открыть порты.** Compute Engine → нужная ВМ → Edit → Firewalls → галки
   «Allow HTTP traffic» и «Allow HTTPS traffic» → Save. Без них Let's Encrypt
   не подтвердит домен, а Telegram не откроет кабинет.
2. **Закрепить IP** (желательно). VPC network → IP addresses → у внешнего адреса
   ВМ сменить тип с Ephemeral на Static. Иначе после перезапуска ВМ адрес
   поменяется, а вместе с ним — домен `sslip.io` и сертификат.
3. **Проверить SSH.** С этой машины должно работать `ssh ЛОГИН@ВНЕШНИЙ_IP`.
   Если нет — добавьте свой публичный ключ: Compute Engine → Metadata →
   SSH keys → Add item.

Дальше — раздел 2. Если Python на сервере окажется старее 3.11, скрипт остановится
и скажет об этом: на Debian 11 нужен либо новый образ, либо `deadsnakes`.

## 1. Какой сервер брать (если понадобится отдельный)

Требования скромные: **1 vCPU, 1–2 ГБ RAM, 10–20 ГБ SSD, Ubuntu 24.04 LTS**.
Это самый дешёвый тариф почти у любого хостера — ориентир 200–500 ₽/мес
(цены меняются, смотрите на сайте).

**Локацию лучше брать зарубежную** (Нидерланды, Германия, Финляндия) — оттуда
`api.telegram.org` доступен напрямую и `BOT_PROXY_URL` остаётся пустым. Хостеры,
которые принимают карты РФ и дают зарубежные площадки: Timeweb Cloud, aeza,
VDSina, RuVDS. Если сервер окажется в РФ и Telegram с него не открывается —
это не тупик: в `.env` есть `BOT_PROXY_URL`, туда прописывается прокси.

Отдельный домен покупать **не нужно**: скрипт по умолчанию берёт адрес вида
`lk.1-2-3-4.sslip.io` (где `1-2-3-4` — IP сервера через дефисы) и выпускает на него настоящий
сертификат Let's Encrypt. Telegram такой Mini App открывает. Если домен есть или
появится — передайте его через `-Domain`, предварительно направив A-запись на IP.

При создании сервера загрузите свой SSH-ключ (публичную часть). Если ключа нет:

```bash
ssh-keygen -t ed25519 -C "bratstvo"
```

Ключ появится в `C:\Users\HOME\.ssh\id_ed25519`, публичная часть — в файле
с расширением `.pub`, её и загружают в панели хостера.

## 2. Развернуть

Одна команда с этой машины (подставьте IP сервера и свою почту — на неё
Let's Encrypt пришлёт письмо, если сертификат перестанет продлеваться):

```bash
powershell -ExecutionPolicy Bypass -File deploy\deploy.ps1 -Server ЛОГИН@IP_СЕРВЕРА -Bootstrap -Email вашапочта@example.com
```

Скрипт упакует код (без `.venv`, `.env`, базы и загруженных документов), зальёт
его в `/opt/bratstvo` и запустит `deploy/setup_server.sh`, который на сервере:

- поставит Python, PostgreSQL, nginx, certbot и шрифты для PDF-отчётов;
- заведёт системного пользователя `bratstvo` и базу с случайным паролем;
- создаст `/opt/bratstvo/.env` с готовыми `DATABASE_URL`, `STORAGE_DIR`, `WEBAPP_URL`;
- создаст отдельные ключи подписи закрытых медиа и шифрования резервных копий,
  применит миграции целостности;
- поставит два systemd-юнита — `bratstvo-bot` и `bratstvo-api`;
- настроит nginx и выпустит HTTPS-сертификат.

Занимает 3–5 минут. Повторный запуск безопасен — скрипт идемпотентен.

## 3. Дописать токен

Единственное, что скрипт не знает, — токен бота и кто здесь superuser:

```bash
ssh ЛОГИН@IP_СЕРВЕРА "sudo nano /opt/bratstvo/.env"
```

Заполните:

- `BOT_TOKEN` — токен от @BotFather;
- `SUPERUSER_TELEGRAM_IDS` — ваш telegram_id (узнать: команда `/whoami` боту),
  чтобы получить полный доступ без инвайт-кода.

`DEV_TELEGRAM_ID` обязан остаться пустым — непустое значение отключает проверку
подписи Telegram, и кабинет открывается любому, кто знает адрес.

Затем перезапустите:

```bash
ssh ЛОГИН@IP_СЕРВЕРА "sudo systemctl restart bratstvo-bot bratstvo-api"
```

## 4. Проверить

```bash
ssh ЛОГИН@IP_СЕРВЕРА "sudo systemctl is-active bratstvo-bot bratstvo-api; curl -s \$(sudo grep API_BASE_URL /opt/bratstvo/.env | cut -d= -f2)/healthz"
```

Ожидаемо: `active`, `active`, `{"status":"ok"}`. Снаружи:

```bash
curl -s https://lk.1-2-3-4.sslip.io/healthz
```

Дальше — `/start` боту: в меню должна появиться кнопка «🏛 Открыть кабинет».

## 5. Завести регионы и назначить роли

```bash
ssh ЛОГИН@IP_СЕРВЕРА "cd /opt/bratstvo && sudo -u bratstvo .venv/bin/python -m scripts.admin region-add 'Москва'"
```

Управленческая роль (`leader`/`coordinator`/`federal`/`cell_leader`) назначается
только человеку, который уже сам прошёл саморегистрацию в боте по ссылке региона
(`Region.application_code` — печатается при создании региона, отдайте её будущему
руководителю). Найдите его id и назначьте роль:

```bash
ssh ЛОГИН@IP_СЕРВЕРА "cd /opt/bratstvo && sudo -u bratstvo .venv/bin/python -m scripts.admin members-search 'Иванов' --region 'Москва'"
```

```bash
ssh ЛОГИН@IP_СЕРВЕРА "cd /opt/bratstvo && sudo -u bratstvo .venv/bin/python -m scripts.admin user-add --member-id 1 --role leader --region 'Москва'"
```

## 6. Обновление после правок кода

```bash
powershell -ExecutionPolicy Bypass -File deploy\deploy.ps1 -Server ЛОГИН@IP_СЕРВЕРА
```

Зальёт изменения, установит зависимости из `requirements.lock` с обязательной
проверкой hashes от пользователя `bratstvo`, применит
идемпотентные миграции и только после этого перезапустит оба сервиса. Новые
таблицы по-прежнему создаёт `init_db`; изменения существующих таблиц выполняют
скрипты в `scripts/`.

## 7. Бэкапы

```bash
ssh ЛОГИН@IP_СЕРВЕРА "printf '30 3 * * * root /opt/bratstvo/deploy/backup.sh\n' | sudo tee /etc/cron.d/bratstvo-backup"
```

Каждую ночь создаётся один зашифрованный AES-256 архив в
`/var/backups/bratstvo`, срок хранения — 14 дней. Ключ находится отдельно в
`/root/.bratstvo_backup_key`. Скопируйте его в защищённое хранилище секретов:
без него восстановление невозможно.

Для второй копии на отдельном диске задайте `BACKUP_REMOTE_DIR` в cron:

```bash
BACKUP_REMOTE_DIR=/mnt/offsite /opt/bratstvo/deploy/backup.sh
```

Проверка и распаковка перед восстановлением:

```bash
openssl enc -d -aes-256-cbc -pbkdf2 \
  -pass file:/root/.bratstvo_backup_key \
  -in bratstvo-ДАТА.tar.gz.enc | tar xzf -
pg_restore -d bratstvo --clean database.dump
```

Если deploy остановился из-за несовпадения nginx, перенесите изменения шаблона
в `/etc/nginx/sites-available/bratstvo`, проверьте `nginx -t`, перезагрузите
nginx и подтвердите применённую версию:

```bash
sha256sum /opt/bratstvo/deploy/nginx-bratstvo.conf | cut -d' ' -f1 \
  | sudo tee /etc/nginx/.bratstvo-template.sha256
```

## 8. Если что-то не работает

| Симптом | Куда смотреть |
|---|---|
| Бот молчит | `journalctl -u bratstvo-bot -n 50` — пустой `BOT_TOKEN` или сеть до Telegram |
| Кабинет не открывается | `journalctl -u bratstvo-api -n 50`, `nginx -t`, `systemctl status nginx` |
| Telegram ругается на HTTPS | `certbot certificates` — сертификат выпущен? домен совпадает с `WEBAPP_URL`? |
| «Не удалось проверить подпись» | `WEBAPP_URL` в `.env` не совпадает с адресом, по которому открыт кабинет |
| В PDF квадраты вместо букв | `REPORT_FONT_PATH=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf` |

Локальный `.env` на сервер не копируйте: `BOT_PROXY_URL` и адрес туннеля там
машинные, на сервере от них всё ломается. `deploy.ps1` его и не отправляет.
