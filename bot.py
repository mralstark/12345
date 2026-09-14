"""Точка входа Telegram-бота (ТЗ §2): команды, уведомления, «Задачи».
Тяжёлые разделы живут в Mini App — см. api/main.py."""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
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


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

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

    # Список команд (/help, /menu и т.п.) — убран совсем, ими не пользовались:
    # то же самое доступно кнопками внутри кабинета. Пустой список явно
    # очищает то, что было выставлено раньше — иначе Telegram хранит старый
    # список на своей стороне. /start остаётся рабочим независимо от этого —
    # встроенная кнопка Telegram у самого чата с ботом.
    await bot.set_my_commands([])

    # Кнопка меню чата (рядом со строкой ввода, всегда на виду) — открывает
    # кабинет в один тап, не дожидаясь /start и инлайн-кнопки под сообщением.
    # Внутри Mini App разбирается сам, кто пришёл: уже зарегистрированному
    # покажет кабинет, новому — те же две кнопки, что и по /start (см.
    # api/auth.py::get_current_user, keyboards/main_menu.py::WELCOME_TEXT).
    if WEBAPP_URL:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="Личный кабинет", web_app=WebAppInfo(url=WEBAPP_URL))
        )

    dp.include_router(start.router)
    dp.include_router(tasks.router)
    dp.include_router(impersonate.router)
    dp.include_router(apply.router)
    dp.include_router(fallback.router)  # всегда последним: ловит всё остальное

    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(notifier_loop(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
