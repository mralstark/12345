import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# Прокси для доступа к api.telegram.org, если он заблокирован напрямую.
# Задаётся явно в .env, чтобы не зависеть от системных HTTP_PROXY/HTTPS_PROXY.
BOT_PROXY_URL = os.getenv("BOT_PROXY_URL") or None

# Публичный HTTPS-адрес Mini App. Telegram открывает WebApp только по HTTPS,
# поэтому для локальной отладки нужен туннель (ngrok/cloudflared).
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip().rstrip("/") or None

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

# Полные ФИО (как в форме регистрации), которым при самостоятельной
# регистрации анкета одобряется автоматически и сразу выдаётся роль
# federal — доверенные люди-администраторы вместо одного технического
# superuser'а (см. api/routers/register.py). Без учёта регистра/пробелов
# по краям, сравнение — по значению из .env, а не по списку в коде: список
# людей с админ-доступом — не то, что должно требовать деплоя, чтобы измениться.
AUTO_FEDERAL_FULL_NAMES = {
    name.strip().lower() for name in os.getenv("AUTO_FEDERAL_FULL_NAMES", "").split(",") if name.strip()
}

# Временная мера (план «Убираем технического superuser», не постоянная
# архитектура): пока руководители регионов не подтверждают анкеты сами, все
# новые анкеты идут одному конкретному человеку — см. api/routers/register.py.
# Пусто — старое поведение (руководителю региона, без него — любому federal).
PRIMARY_REVIEWER_FULL_NAME = os.getenv("PRIMARY_REVIEWER_FULL_NAME", "").strip()

API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")

# Максимальный размер загружаемого документа (модуль «Документы»).
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "25")) * 1024 * 1024


def require_bot_token() -> str:
    """Токен нужен и боту, и API (проверка подписи initData). Отдельная функция,
    чтобы импорт config не падал в тестах, где токен не задан."""
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не найден. Скопируйте .env.example в .env и укажите токен бота.")
    return BOT_TOKEN
