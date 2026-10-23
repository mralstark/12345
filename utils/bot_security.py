"""Reject disabled real accounts before any bot handler or pending FSM action."""
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, TelegramObject

from database.db import async_session
from utils.users import get_user_by_telegram_id


class ActiveBotUserMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        actor = data.get("event_from_user") or getattr(event, "from_user", None)
        if actor is None:
            return await handler(event, data)
        async with async_session() as session:
            real = await get_user_by_telegram_id(session, actor.id)
            disabled = real is not None and not real.is_active
        # Unknown accounts must still reach registration and identity confirmation.
        # Checking the real actor also prevents an active view-as target bypass.
        if not disabled:
            return await handler(event, data)
        state = data.get("state")
        if state is not None:
            await state.clear()
        text = "Доступ к кабинету закрыт. Обратитесь к администратору."
        if isinstance(event, CallbackQuery):
            await event.answer(text, show_alert=True)
        else:
            await event.answer(text)
        return None
