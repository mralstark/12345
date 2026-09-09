"""Ответ на всё, что не разобрали остальные роутеры. Подключается последним."""

from aiogram import Router
from aiogram.types import Message

from database.db import async_session
from handlers.common import NO_ACCESS_TEXT, PENDING_APPLICATION_TEXT, show_main_menu
from utils.counters import has_pending_application
from utils.users import resolve_user

router = Router()


@router.message()
async def unknown_message(message: Message) -> None:
    async with async_session() as session:
        user = await resolve_user(session, message.from_user.id, message.from_user.full_name)
        if user is None:
            if await has_pending_application(session, message.from_user.id):
                await message.answer(PENDING_APPLICATION_TEXT)
                return
            await message.answer(NO_ACCESS_TEXT, parse_mode="HTML")
            return
        await message.answer("Не понял команду. Вот главное меню:")
        await show_main_menu(message, session, user)
