"""Ограничение частоты входящих сообщений и нажатий в Telegram-боте."""

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from api.rate_limit import SlidingWindowLimiter


class BotRateLimitMiddleware(BaseMiddleware):
    """Защищает polling-бот от спама до обращений к базе данных."""

    def __init__(self) -> None:
        self._limiter = SlidingWindowLimiter(max_keys=20_000)
        self._notices = SlidingWindowLimiter(max_keys=20_000)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        actor = data.get("event_from_user") or getattr(event, "from_user", None)
        if actor is None:
            return await handler(event, data)

        # Сначала общий предел: при потоке с множества аккаунтов не создаём
        # ответ на каждый отброшенный update и не усиливаем атаку исходящими
        # запросами к Telegram.
        if self._limiter.check("bot-global", 0, limit=1_200, window_seconds=60) is not None:
            return None

        if self._limiter.check("bot-user", int(actor.id), limit=60, window_seconds=60) is None:
            return await handler(event, data)

        # Одно понятное уведомление раз в десять секунд, остальные события
        # того же пользователя отбрасываются без обращений к БД.
        if self._notices.check("bot-notice", int(actor.id), limit=1, window_seconds=10) is None:
            text = "Слишком много действий. Подождите несколько секунд."
            if isinstance(event, CallbackQuery):
                await event.answer(text, show_alert=True)
            elif isinstance(event, Message):
                await event.answer(text)
        return None
