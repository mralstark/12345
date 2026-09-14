import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
IS_PRODUCTION = APP_ENV == "production"

# Прокси для доступа к api.telegram.org, если он заблокирован напрямую.
# Задаётся явно в .env, чтобы не зависеть от системных HTTP_PROXY/HTTPS_PROXY.
BOT_PROXY_URL = os.getenv("BOT_PROXY_URL") or None

# Публичный HTTPS-адрес Mini App. Telegram открывает WebApp только по HTTPS,
# поэтому для локальной отладки нужен туннель (ngrok/cloudflared).
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip().rstrip("/") or None

# Внешние адреса, с которых браузеру разрешено обращаться к API. В обычной
# установке Mini App и API находятся на одном origin, поэтому CORS вообще не
# нужен. Список существует только для явно настроенной локальной разработки.
CORS_ALLOWED_ORIGINS = tuple(
    origin.strip().rstrip("/")
    for origin in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
)

_webapp_host = urlparse(WEBAPP_URL).hostname if WEBAPP_URL else None
_development_hosts = ("localhost", "127.0.0.1", "test") if not IS_PRODUCTION else ()
ALLOWED_HOSTS = tuple(
    dict.fromkeys(
        host
        for host in (
            *(item.strip() for item in os.getenv("ALLOWED_HOSTS", "").split(",")),
            _webapp_host,
            *_development_hosts,
        )
        if host
    )
)

# По умолчанию SQLite — чтобы проект поднимался локально без установки Postgres.
# Прод: postgresql+asyncpg://... (см. .env.example).
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite+aiosqlite:///{BASE_DIR / 'bratstvo.db'}")

# Часовой пояс, в котором считается «сегодня»: границы дня, дни рождения, дедлайны.
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Europe/Moscow")

STORAGE_DIR = Path(os.getenv("STORAGE_DIR") or (BASE_DIR / "storage"))

# Технические superuser'ы: полный доступ ко всем регионам без инвайт-кода.
# Задел на переходный период (план «Убираем технического superuser») — новых
# так больше не заводим, доступ отдаётся именным федеральным (см. ниже).
SUPERUSER_TELEGRAM_IDS = {
    int(x) for x in os.getenv("SUPERUSER_TELEGRAM_IDS", "").replace(" ", "").split(",") if x
}

# Автоматическое одобрение доверенных федеральных координаторов допустимо
# только по Telegram ID из подписанного initData. ФИО вводится самим
# заявителем и никогда не является идентификатором или фактором доверия.
AUTO_FEDERAL_TELEGRAM_IDS = {
    int(value)
    for value in os.getenv("AUTO_FEDERAL_TELEGRAM_IDS", "").replace(" ", "").split(",")
    if value
}

# Отдельный ключ для подписанных ссылок на закрытые фотографии и аватары.
# В development для обратной совместимости используется BOT_TOKEN; в
# production отсутствие отдельного ключа считается ошибкой конфигурации.
MEDIA_SIGNING_KEY = os.getenv("MEDIA_SIGNING_KEY", "").strip()

# Временная мера (план «Убираем технического superuser», не постоянная
# архитектура): пока руководители регионов не подтверждают анкеты сами, все
# новые анкеты идут одному конкретному человеку — см. api/routers/register.py.
# Пусто — старое поведение (руководителю региона, без него — любому federal).
PRIMARY_REVIEWER_FULL_NAME = os.getenv("PRIMARY_REVIEWER_FULL_NAME", "").strip()

API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")

# Максимальный размер загружаемого документа (модуль «Документы»).
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "25")) * 1024 * 1024
MAX_STORAGE_BYTES = int(os.getenv("MAX_STORAGE_MB", "5120")) * 1024 * 1024
MIN_FREE_STORAGE_BYTES = int(os.getenv("MIN_FREE_STORAGE_MB", "512")) * 1024 * 1024


def validate_security_config() -> None:
    """Не позволяем production запуститься с отладочным обходом или с
    устаревшей настройкой, выдающей админские права по пользовательскому ФИО."""
    if APP_ENV not in {"development", "test", "production"}:
        raise RuntimeError("APP_ENV должен быть development, test или production")
    if not IS_PRODUCTION:
        return
    if os.getenv("DEV_TELEGRAM_ID", "").strip():
        raise RuntimeError("DEV_TELEGRAM_ID запрещён при APP_ENV=production")
    if os.getenv("AUTO_FEDERAL_FULL_NAMES", "").strip():
        raise RuntimeError(
            "AUTO_FEDERAL_FULL_NAMES небезопасен и больше не поддерживается; "
            "используйте AUTO_FEDERAL_TELEGRAM_IDS"
        )
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN обязателен при APP_ENV=production")
    if not MEDIA_SIGNING_KEY:
        raise RuntimeError("MEDIA_SIGNING_KEY обязателен при APP_ENV=production")
    webapp = urlparse(WEBAPP_URL or "")
    if webapp.scheme != "https" or not webapp.hostname or webapp.username or webapp.password:
        raise RuntimeError("WEBAPP_URL в production должен быть корректным HTTPS origin")
    if not ALLOWED_HOSTS:
        raise RuntimeError("ALLOWED_HOSTS не определён: задайте WEBAPP_URL или ALLOWED_HOSTS")
    if MAX_STORAGE_BYTES <= MAX_UPLOAD_BYTES:
        raise RuntimeError("MAX_STORAGE_MB должен быть больше MAX_UPLOAD_MB")
    if MIN_FREE_STORAGE_BYTES < MAX_UPLOAD_BYTES:
        raise RuntimeError("MIN_FREE_STORAGE_MB должен быть не меньше MAX_UPLOAD_MB")
    if any(host == "*" or host.startswith("*.") or "://" in host for host in ALLOWED_HOSTS):
        raise RuntimeError("ALLOWED_HOSTS в production должен содержать только точные имена хостов")
    for origin in CORS_ALLOWED_ORIGINS:
        parsed = urlparse(origin)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise RuntimeError("CORS_ALLOWED_ORIGINS в production должен содержать точные HTTPS origin")


def require_bot_token() -> str:
    """Токен нужен и боту, и API (проверка подписи initData). Отдельная функция,
    чтобы импорт config не падал в тестах, где токен не задан."""
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не найден. Скопируйте .env.example в .env и укажите токен бота.")
    return BOT_TOKEN
