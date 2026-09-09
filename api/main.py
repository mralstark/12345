"""Бэкенд Mini App (ТЗ §2). Общая с ботом БД и общий код в services/ и utils/.

Запуск:  uvicorn api.main:app --host 0.0.0.0 --port 8000
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from api.routers import (
    analytics,
    applications,
    bureau,
    cells,
    character,
    context,
    documents,
    event_tasks,
    events,
    finance,
    members,
    news,
    profile,
    quests,
    register,
    regions,
    reports,
    shop,
    tasks,
    universities,
)
from config import BASE_DIR, STORAGE_DIR
from database.db import init_db
from utils.access import AccessDenied
from utils.notify import close_notifier_bot

logger = logging.getLogger(__name__)

WEBAPP_DIR = BASE_DIR / "webapp"
LANDING_DIR = BASE_DIR / "landing"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    yield
    await close_notifier_bot()


app = FastAPI(title="Личный кабинет Братства Академистов", lifespan=lifespan)

# Mini App грузится с того же хоста, но при отладке через туннель источник может
# отличаться — заголовок Authorization всё равно проверяется подписью Telegram.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(AccessDenied)
async def access_denied_handler(request: Request, exc: AccessDenied) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


for router in (
    context.router,
    applications.router,
    bureau.router,
    members.router,
    finance.router,
    events.router,
    event_tasks.router,
    documents.router,
    cells.router,
    universities.router,
    profile.router,
    register.router,
    regions.router,
    tasks.router,
    news.router,
    analytics.router,
    reports.router,
    character.router,
    quests.router,
    shop.router,
):
    app.include_router(router, prefix="/api")


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


def _asset_version(name: str) -> int:
    return int((WEBAPP_DIR / name).stat().st_mtime)


if LANDING_DIR.exists():
    # Отдельная статичная страница-шлюз: архивный снимок Братства в герое,
    # прокрутка проявляет его и приводит к ссылке на бота. Не путать с самим
    # Mini App ниже: WEBAPP_URL по-прежнему указывает на корень "/".
    app.mount("/welcome", StaticFiles(directory=LANDING_DIR, html=True), name="landing")

if WEBAPP_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEBAPP_DIR), name="static")

    @app.get("/webapp-version")
    async def webapp_version() -> JSONResponse:
        """Для самопроверки версии в app.js (см. webapp/app.js, самое начало
        файла) — некоторые клиенты Telegram (замечено на телефонах) держат
        собственный кэш WebView настолько цепко, что не спасают ни версия в
        адресе app.js, ни Cache-Control: no-store на index.html, ни даже
        очистка кэша в настройках Telegram — WebView просто не перезагружается
        заново. Этот эндпоинт всегда бьёт по сети напрямую (сам no-store),
        и app.js при расхождении версий сам форсирует жёсткий переход на новый
        адрес — так до WebView достучаться получается, когда обычное
        кэширование HTTP бессильно."""
        return JSONResponse({"version": _asset_version("app.js")}, headers={"Cache-Control": "no-store"})

    @app.get("/")
    async def index() -> HTMLResponse:
        """Подставляем в ссылки на app.js/styles.css версию по mtime файла —
        адрес меняется сам при каждом деплое. index.html отдаём с no-store."""
        html = (WEBAPP_DIR / "index.html").read_text(encoding="utf-8")
        for asset in ("app.js", "styles.css"):
            html = html.replace(f"/static/{asset}", f"/static/{asset}?v={_asset_version(asset)}")
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})
