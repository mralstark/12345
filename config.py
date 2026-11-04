import os
import json
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

_runtime_token = os.getenv("DATA_RUNTIME_CIPHERTEXT", "").strip()
_runtime_secrets = {}
_runtime_names = {"BOT_TOKEN", "DATABASE_URL", "MEDIA_SIGNING_KEY", "BOT_PROXY_URL", "VK_MINI_APP_SECRET"}
if _runtime_token:
    from utils.data_crypto import DataEncryptionError, decrypt_bytes
    try:
        _runtime_secrets = json.loads(decrypt_bytes(_runtime_token, "runtime-secrets"))
        if not isinstance(_runtime_secrets, dict) or not set(_runtime_secrets) <= _runtime_names:
            raise ValueError
        if any(not isinstance(value, str) or len(value) > 8192 for value in _runtime_secrets.values()):
            raise ValueError
        if any(not _runtime_secrets.get(name, "").strip() for name in ("BOT_TOKEN", "DATABASE_URL", "MEDIA_SIGNING_KEY")):
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise DataEncryptionError("Invalid encrypted runtime configuration") from None


def _private_setting(name: str, default: str = "") -> str:
    return _runtime_secrets.get(name, os.getenv(name, default))


BOT_TOKEN = _private_setting("BOT_TOKEN").strip()
APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
IS_PRODUCTION = APP_ENV == "production"

# Прокси для доступа к api.telegram.org, если он заблокирован напрямую.
# Задаётся явно в .env, чтобы не зависеть от системных HTTP_PROXY/HTTPS_PROXY.
BOT_PROXY_URL = _private_setting("BOT_PROXY_URL") or None

# Публичный HTTPS-адрес Mini App. Telegram открывает WebApp только по HTTPS,
# поэтому для локальной отладки нужен туннель (ngrok/cloudflared).
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip().rstrip("/") or None
# Public VK ID application identifier; tokens are exchanged only by the API.
VK_ID_CLIENT_ID = os.getenv("VK_ID_CLIENT_ID", "").strip()
VK_MINI_APP_ID = os.getenv("VK_MINI_APP_ID", "").strip()
VK_MINI_APP_SECRET = _private_setting("VK_MINI_APP_SECRET").strip()

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
DATABASE_URL = _private_setting("DATABASE_URL", f"sqlite+aiosqlite:///{BASE_DIR / 'bratstvo.db'}")

# Часовой пояс, в котором считается «сегодня»: границы дня, дни рождения, дедлайны.
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Europe/Moscow")

STORAGE_DIR = Path(os.getenv("STORAGE_DIR") or (BASE_DIR / "storage"))
BOT_HEALTH_FILE = Path(os.getenv("BOT_HEALTH_FILE") or (
    "/var/lib/bratstvo-bot/health.json" if IS_PRODUCTION and os.name == "posix" else STORAGE_DIR / ".bot-health.json"
))
# Explicit opt-in; never infer a recipient from roles or personal contacts.
OPERATIONS_ALERT_TELEGRAM_ID = os.getenv("OPERATIONS_ALERT_TELEGRAM_ID", "").strip()

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
MEDIA_SIGNING_KEY = _private_setting("MEDIA_SIGNING_KEY").strip()

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

# Administrative identifiers are personal data too. The deployment migrator seals
# these legacy env settings; only their ciphertext travels with a hosted config.
_bootstrap_token = os.getenv("DATA_BOOTSTRAP_CIPHERTEXT", "").strip()
if _bootstrap_token:
    from utils.data_crypto import DataEncryptionError, decrypt_bytes
    try:
        _bootstrap = json.loads(decrypt_bytes(_bootstrap_token, "runtime-bootstrap"))
        _super = _bootstrap.get("SUPERUSER_TELEGRAM_IDS", [])
        _federal = _bootstrap.get("AUTO_FEDERAL_TELEGRAM_IDS", [])
        if any(not isinstance(values, list) or len(values) > 100 for values in (_super, _federal)):
            raise ValueError
        if any(not isinstance(value, int) or isinstance(value, bool) or not 0 < value < 2**63 for value in _super + _federal):
            raise ValueError
        SUPERUSER_TELEGRAM_IDS = set(_super)
        AUTO_FEDERAL_TELEGRAM_IDS = set(_federal)
        PRIMARY_REVIEWER_FULL_NAME = _bootstrap.get("PRIMARY_REVIEWER_FULL_NAME", "")
        OPERATIONS_ALERT_TELEGRAM_ID = _bootstrap.get("OPERATIONS_ALERT_TELEGRAM_ID", "")
        if not isinstance(PRIMARY_REVIEWER_FULL_NAME, str) or len(PRIMARY_REVIEWER_FULL_NAME) > 256:
            raise ValueError
        if not isinstance(OPERATIONS_ALERT_TELEGRAM_ID, str):
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise DataEncryptionError("Invalid encrypted bootstrap configuration") from None


def validate_security_config() -> None:
    """Не позволяем production запуститься с отладочным обходом или с
    устаревшей настройкой, выдающей админские права по пользовательскому ФИО."""
    if OPERATIONS_ALERT_TELEGRAM_ID:
        value = OPERATIONS_ALERT_TELEGRAM_ID.removeprefix("-")
        if not value.isascii() or not value.isdigit() or not 1 <= len(value) <= 19 or not 0 < int(value) < 2**63:
            raise RuntimeError("OPERATIONS_ALERT_TELEGRAM_ID должен быть числовым ID получателя")
    if VK_MINI_APP_ID or VK_MINI_APP_SECRET:
        if (not VK_MINI_APP_ID or len(VK_MINI_APP_ID) > 20 or
                not all(c in "0123456789" for c in VK_MINI_APP_ID) or int(VK_MINI_APP_ID) <= 0):
            raise RuntimeError("VK_MINI_APP_ID должен быть числовым ID мини-приложения")
        if len(VK_MINI_APP_SECRET) < 16:
            raise RuntimeError("Для VK Mini App нужен защищённый ключ приложения")
    if APP_ENV not in {"development", "test", "production"}:
        raise RuntimeError("APP_ENV должен быть development, test или production")
    if VK_ID_CLIENT_ID and (len(VK_ID_CLIENT_ID) > 20 or not all(c in "0123456789" for c in VK_ID_CLIENT_ID) or int(VK_ID_CLIENT_ID) <= 0):
        raise RuntimeError("VK_ID_CLIENT_ID должен быть публичным числовым ID приложения VK ID")
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
    from utils.data_crypto import keyring
    keyring()
    if not _runtime_token or any(os.getenv(name, "").strip() for name in _runtime_names):
        raise RuntimeError("Seal production credentials into DATA_RUNTIME_CIPHERTEXT and clear plaintext copies")
    if any(os.getenv(name, "").strip() for name in (
        "SUPERUSER_TELEGRAM_IDS", "AUTO_FEDERAL_TELEGRAM_IDS",
        "PRIMARY_REVIEWER_FULL_NAME", "OPERATIONS_ALERT_TELEGRAM_ID",
    )):
        raise RuntimeError("Seal personal deployment settings into DATA_BOOTSTRAP_CIPHERTEXT")


def require_bot_token() -> str:
    """Токен нужен и боту, и API (проверка подписи initData). Отдельная функция,
    чтобы импорт config не падал в тестах, где токен не задан."""
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не найден. Скопируйте .env.example в .env и укажите токен бота.")
    return BOT_TOKEN
