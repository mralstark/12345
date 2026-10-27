"""Точка входа Telegram-бота (ТЗ §2): команды, уведомления, «Задачи».
Тяжёлые разделы живут в Mini App — см. api/main.py."""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramNetworkError
from aiogram.types import MenuButtonWebApp, WebAppInfo

from config import (
    BOT_PROXY_URL,
    WEBAPP_URL,
    require_bot_token,
    validate_security_config,
)
from database.db import init_db
from handlers import apply, fallback, impersonate, start, tasks
from services.notifier import notifier_loop
from utils.bot_rate_limit import BotRateLimitMiddleware
from utils.bot_security import ActiveBotUserMiddleware


async def retry_startup_network(operation, attempts=5):
    """Startup operations are idempotent; retry transport failures only."""
    for attempt in range(attempts):
        try:
            # Preserve aiogram's normal 60-second transport budget, with an outer bound.
            return await asyncio.wait_for(operation(), timeout=65)
        except (TelegramNetworkError, TimeoutError):
            if attempt + 1 == attempts:
                raise
            logging.warning("Telegram startup transport retry %s/%s", attempt + 1, attempts)
            await asyncio.sleep(min(2 ** (attempt + 1), 16))


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from utils.safe_logging import install_secret_redaction
    install_secret_redaction()

    validate_security_config()
    token = require_bot_token()
    await init_db()

    if BOT_PROXY_URL:
        # URL прокси может содержать логин и пароль — в journald они попадать не должны.
        logging.info("Для Telegram настроен прокси")
    if not WEBAPP_URL:
        logging.warning("WEBAPP_URL не задан — кнопка «Открыть кабинет» в меню бота не появится.")

    session = AiohttpSession(proxy=BOT_PROXY_URL) if BOT_PROXY_URL else None
    bot = Bot(token=token, session=session)
    dp = Dispatcher()
    rate_limit = BotRateLimitMiddleware()
    dp.message.outer_middleware(rate_limit)
    dp.callback_query.outer_middleware(rate_limit)
    # Check the real Telegram actor before handlers, FSM or impersonation.
    active_user = ActiveBotUserMiddleware()
    dp.message.outer_middleware(active_user)
    dp.callback_query.outer_middleware(active_user)

    # Список команд (/help, /menu и т.п.) — убран совсем, ими не пользовались:
    # то же самое доступно кнопками внутри кабинета. Пустой список явно
    # очищает то, что было выставлено раньше — иначе Telegram хранит старый
    # список на своей стороне. /start остаётся рабочим независимо от этого —
    # встроенная кнопка Telegram у самого чата с ботом.
    await retry_startup_network(lambda: bot.set_my_commands([]))

    # Кнопка меню чата (рядом со строкой ввода, всегда на виду) — открывает
    # кабинет в один тап, не дожидаясь /start и инлайн-кнопки под сообщением.
    # Внутри Mini App разбирается сам, кто пришёл: уже зарегистрированному
    # покажет кабинет, новому — те же две кнопки, что и по /start (см.
    # api/auth.py::get_current_user, keyboards/main_menu.py::WELCOME_TEXT).
    if WEBAPP_URL:
        await retry_startup_network(lambda: bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="Личный кабинет", web_app=WebAppInfo(url=WEBAPP_URL))
        ))

    dp.include_router(start.router)
    dp.include_router(tasks.router)
    dp.include_router(impersonate.router)
    dp.include_router(apply.router)
    dp.include_router(fallback.router)  # всегда последним: ловит всё остальное

    # Deployments must not discard /start, login confirmations or other messages
    # received while the service was restarting.
    await retry_startup_network(lambda: bot.delete_webhook(drop_pending_updates=False))
    # Cache the authenticated bot identity before polling; aiogram reuses it.
    await retry_startup_network(bot.me)
    asyncio.create_task(notifier_loop(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
