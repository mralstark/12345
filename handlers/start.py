"""Вход в кабинет: /start, в том числе с deep-link-ссылкой региона на
саморегистрацию (handlers/apply.py)."""

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from sqlalchemy import select

from database.db import async_session
from database.models import (
    APPLICATION_STATE_PENDING,
    Member,
    MembershipApplication,
    Region,
    TelegramPhoneVerification,
)
from handlers.apply import start_application
from handlers.common import PENDING_APPLICATION_TEXT, show_main_menu
from keyboards.main_menu import WELCOME_TEXT, register_welcome_keyboard
from utils.counters import has_pending_application
from utils.invites import parse_application_payload
from utils.parser import normalize_phone
from utils.users import resolve_user

router = Router()
logger = logging.getLogger(__name__)


@router.message(Command("phone"))
async def request_verified_phone(message: Message) -> None:
    """Запасной путь для старых клиентов без WebApp.requestContact."""
    await message.answer(
        "Нажмите кнопку ниже. Telegram передаст боту только ваш номер после подтверждения.",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="📱 Передать мой номер", request_contact=True)]],
            resize_keyboard=True,
            one_time_keyboard=True,
        ),
    )


@router.message(F.contact)
async def save_verified_phone(message: Message) -> None:
    """Принимает только собственный контакт пользователя.

    Telegram сам проставляет ``contact.user_id`` для номера владельца. Это
    отличает подтверждённый номер от произвольного контакта из адресной книги.
    """
    contact = message.contact
    if contact is None or contact.user_id != message.from_user.id:
        await message.answer("Отправьте именно свой номер телефона.", reply_markup=ReplyKeyboardRemove())
        return
    try:
        phone = normalize_phone(contact.phone_number)
    except ValueError:
        await message.answer("Telegram передал номер в неизвестном формате.", reply_markup=ReplyKeyboardRemove())
        return

    async with async_session() as session:
        verification = await session.get(TelegramPhoneVerification, message.from_user.id)
        if verification is None:
            verification = TelegramPhoneVerification(telegram_id=message.from_user.id, phone=phone)
            session.add(verification)
        else:
            verification.phone = phone
        user = await resolve_user(session, message.from_user.id, message.from_user.full_name)
        if user is not None:
            user.phone = phone
            member = await session.get(Member, user.member_id) if user.member_id else None
            if member is not None:
                member.phone = phone
        await session.commit()

    await message.answer(
        "✅ Номер подтверждён. Вернитесь в приложение — поле заполнится автоматически.",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(CommandStart(deep_link=True))
async def start_with_application(message: Message, command: CommandObject) -> None:
    """APPLY_-ссылка региона (публичная саморегистрация, handlers/apply.py) —
    многоразовый код, не привязан ни к одному конкретному User. Откатывается
    на обычный /start, если payload не распознан."""
    code = parse_application_payload(command.args)
    if code is None:
        await start_plain(message)
        return

    async with async_session() as session:
        region = (await session.execute(select(Region).where(Region.application_code == code))).scalar_one_or_none()
        if region is None:
            await message.answer(
                "🚫 Ссылка недействительна. Попросите руководителя региона выслать актуальную.",
                parse_mode="HTML",
            )
            return

        existing = await resolve_user(session, message.from_user.id, message.from_user.full_name)
        if existing is not None:
            await show_main_menu(message, session, existing)
            return

        pending = (
            await session.execute(
                select(MembershipApplication).where(
                    MembershipApplication.telegram_id == message.from_user.id,
                    MembershipApplication.region_id == region.id,
                    MembershipApplication.state == APPLICATION_STATE_PENDING,
                )
            )
        ).scalar_one_or_none()
        if pending is not None:
            await message.answer("📝 Ваш личный кабинет уже создан и ждёт подтверждения руководителя региона.")
            return

    await start_application(message, region)


@router.message(CommandStart())
async def start_plain(message: Message) -> None:
    async with async_session() as session:
        user = await resolve_user(session, message.from_user.id, message.from_user.full_name)
        if user is None:
            if await has_pending_application(session, message.from_user.id):
                await message.answer(PENDING_APPLICATION_TEXT)
                return
            # Незнакомый telegram_id — два разных пути (план «Снизу вверх», §1):
            # отбор на сайте (вне этого проекта) и создание своего личного
            # кабинета в нашей форме (для уже принятого человека).
            await message.answer(WELCOME_TEXT, reply_markup=register_welcome_keyboard(), parse_mode="HTML")
            return
        await show_main_menu(message, session, user)


@router.callback_query(F.data == "menu:main")
async def back_to_menu(callback: CallbackQuery, state: FSMContext) -> None:
    # "В меню"/"Отмена" — общий выход из любого FSM-диалога (задачи,
    # создание аккаунта и т.д.), поэтому сбрасываем состояние здесь одним
    # местом, а не в каждом хендлере отдельно.
    await state.clear()
    async with async_session() as session:
        user = await resolve_user(session, callback.from_user.id, callback.from_user.full_name)
        if user is None:
            await callback.answer("Доступ не открыт", show_alert=True)
            return
        await show_main_menu(callback, session, user)


@router.message(Command("whoami"))
async def whoami(message: Message) -> None:
    """Служебная команда: показывает telegram_id — им заполняется SUPERUSER_TELEGRAM_IDS."""
    await message.answer(
        f"Ваш Telegram ID: <code>{message.from_user.id}</code>",
        parse_mode="HTML",
    )
