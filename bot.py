"""Точка входа Telegram-бота (ТЗ §2): команды, уведомления, «Задачи».
Тяжёлые разделы живут в Mini App — см. api/main.py."""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramNetworkError
from aiogram.types import MenuButtonWebApp, WebAppInfo

from config import (
    BOT_PROXY_URL,
    BOT_HEALTH_FILE,
    WEBAPP_URL,
    require_bot_token,
    validate_security_config,
)
from database.db import init_db
from handlers import apply, fallback, impersonate, start, tasks
from services.notifier import notifier_loop
from utils.bot_rate_limit import BotRateLimitMiddleware
from utils.bot_security import ActiveBotUserMiddleware
from utils.bot_health import BotHealth, TelegramHealthMiddleware, UpdateHealthMiddleware
from utils.telegram_transport import create_bot_session


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


async def configure_menu(bot, health):
    """Optional idempotent setup must never block receiving user messages."""
    operations = {}
    if WEBAPP_URL:
        operations["menu"] = lambda: bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="Личный кабинет", web_app=WebAppInfo(url=WEBAPP_URL)))
    operations["commands"] = lambda: bot.set_my_commands([])
    try:
        while operations:
            for name, operation in list(operations.items()):
                try:
                    await retry_startup_network(operation)
                except Exception as exc:
                    if name not in health.data["setup_errors"]:
                        health.data["setup_errors"].append(name)
                    logging.error("Telegram optional setup failed: %s %s", name, type(exc).__name__)
                    if not isinstance(exc, (TelegramNetworkError, TimeoutError)):
                        del operations[name]
                else:
                    del operations[name]
                    if name in health.data["setup_errors"]:
                        health.data["setup_errors"].remove(name)
            if operations:
                await asyncio.sleep(60)
    finally:
        health.data["setup_pending"] = False


async def observed_notifier(bot, health):
    health.data["notifier_running"] = True
    try:
        await notifier_loop(bot)
    finally:
        health.data["notifier_running"] = False


async def run_bot(bot, dp, health):
    health.write()
    tasks = [asyncio.create_task(health.heartbeat())]
    try:
        await init_db()
        # Only authenticated identity and disabling a webhook are prerequisites.
        # Preserve the original transport timeout and every pending update.
        await retry_startup_network(bot.me)
        await retry_startup_network(lambda: bot.delete_webhook(drop_pending_updates=False))
        tasks.append(asyncio.create_task(configure_menu(bot, health)))
        tasks.append(asyncio.create_task(observed_notifier(bot, health)))
        await dp.start_polling(bot, polling_timeout=25, close_bot_session=False)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        health.data["phase"] = "stopped"
        try:
            health.write()
        except OSError:
            logging.error("Unable to write final bot health state")
        await bot.session.close()


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from utils.safe_logging import install_secret_redaction
    install_secret_redaction()

    validate_security_config()
    token = require_bot_token()

    if BOT_PROXY_URL:
        # URL прокси может содержать логин и пароль — в journald они попадать не должны.
        logging.info("Для Telegram настроен прокси")
    if not WEBAPP_URL:
        logging.warning("WEBAPP_URL не задан — кнопка «Открыть кабинет» в меню бота не появится.")

    health = BotHealth(BOT_HEALTH_FILE)
    session = create_bot_session(proxy=BOT_PROXY_URL)
    session.middleware.register(TelegramHealthMiddleware(health))
    bot = Bot(token=token, session=session)
    dp = Dispatcher()
    dp.update.outer_middleware(UpdateHealthMiddleware(health))
    rate_limit = BotRateLimitMiddleware()
    dp.message.outer_middleware(rate_limit)
    dp.callback_query.outer_middleware(rate_limit)
    # Check the real Telegram actor before handlers, FSM or impersonation.
    active_user = ActiveBotUserMiddleware()
    dp.message.outer_middleware(active_user)
    dp.callback_query.outer_middleware(active_user)

    dp.include_router(start.router)
    dp.include_router(tasks.router)
    dp.include_router(impersonate.router)
    dp.include_router(apply.router)
    dp.include_router(fallback.router)  # всегда последним: ловит всё остальное

    await run_bot(bot, dp, health)


if __name__ == "__main__":
    asyncio.run(main())
