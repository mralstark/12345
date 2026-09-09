"""Подтверждение личного кабинета.

Саморегистрация целиком переехала в веб-форму Mini App (api/routers/register.py,
webapp/app.js::renderRegisterView) — человек создаёт себе анкету там, а не в
чате бота. Здесь остаётся только сторона проверяющего: анкета (по-прежнему
модель MembershipApplication, только текст для людей теперь не «заявка на
вступление», а «подтверждение личного кабинета» — ниже не переименовываем
таблицу/поля, меняем только то, что видит человек) приходит в личные
сообщения с кнопками «Подтвердить» / «Исправить» / «Отклонить». Временно
(план «Убираем технического superuser») проверяют не руководители регионов,
а федеральные координаторы — см. _require_reviewer,
config.PRIMARY_REVIEWER_FULL_NAME."""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from database.db import async_session
from database.models import (
    APPLICATION_STATE_LABELS,
    APPLICATION_STATE_PENDING,
    EDUCATION_LEVEL_LABELS,
    MEMBER_STATUS_LABELS,
    ROLE_FEDERAL,
    ROLE_SUPERUSER,
    MembershipApplication,
    Region,
    University,
    User,
)
from keyboards.main_menu import WELCOME_TEXT, register_welcome_keyboard
from services.admin_actions import regenerate_application_code
from services.applications import approve_application as svc_approve_application
from services.applications import reject_application as svc_reject_application
from utils.invites import application_link
from utils.notify import notify_telegram, send_cabinet_welcome
from utils.parser import parse_date_hint
from utils.tz import today as tz_today
from utils.users import resolve_user

router = Router()


class ApplicationEditStates(StatesGroup):
    waiting_value = State()


async def start_application(message: Message, region: Region) -> None:
    """Точка входа — зовётся из handlers/start.py, когда APPLY_-код региона
    разобрался в конкретное отделение и у этого telegram_id ещё нет ни
    аккаунта, ни анкеты. Регион уже известен, поэтому форма регистрации
    откроется с пропущенным шагом выбора отделения (см. register_welcome_keyboard)."""
    await message.answer(WELCOME_TEXT, reply_markup=register_welcome_keyboard(region.id), parse_mode="HTML")


# --- Рассмотрение руководителем ---------------------------------------------


async def _require_reviewer(callback: CallbackQuery, application: MembershipApplication) -> User | None:
    """Временно (план «Убираем технического superuser», см. также
    config.PRIMARY_REVIEWER_FULL_NAME) подтверждение анкет — не у
    руководителей регионов, а у федеральных координаторов: пока не решили
    вернуть это руководителям, подтверждают federal/superuser."""
    async with async_session() as session:
        user = await resolve_user(session, callback.from_user.id, callback.from_user.full_name)
        if user is None:
            await callback.answer("Доступ не открыт", show_alert=True)
            return None
        if user.role not in (ROLE_FEDERAL, ROLE_SUPERUSER):
            await callback.answer("Доступно только администраторам", show_alert=True)
            return None
        return user


def _review_keyboard(application_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"apply:approve:{application_id}")],
            [InlineKeyboardButton(text="✏️ Исправить", callback_data=f"apply:edit:{application_id}")],
            [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"apply:reject:{application_id}")],
        ]
    )


async def _render_application_text(application: MembershipApplication) -> str:
    async with async_session() as session:
        region = await session.get(Region, application.region_id)
        university = await session.get(University, application.university_id) if application.university_id else None
    lines = [f"ФИО: {application.full_name}"]
    if application.phone:
        lines.append(f"Телефон: {application.phone}")
    if application.birth_date:
        lines.append(f"Дата рождения: {application.birth_date.strftime('%d.%m.%Y')}")
    if university:
        lines.append(f"ВУЗ: {university.name}")
    if application.faculty:
        lines.append(f"Факультет: {application.faculty}")
    if application.course:
        lines.append(f"Курс: {application.course}")
    if application.education_level:
        lines.append(f"Уровень: {EDUCATION_LEVEL_LABELS.get(application.education_level, application.education_level)}")
    if application.workplace:
        lines.append(f"Место работы: {application.workplace}")
    lines.append(f"Статус в составе: {MEMBER_STATUS_LABELS.get(application.member_status, application.member_status)}")
    lines.append(f"\nСтатус заявки: {APPLICATION_STATE_LABELS[application.state]}")
    return f"📝 <b>Подтверждение личного кабинета — {region.name if region else '?'}</b>\n\n" + "\n".join(lines)


async def _send_pending_applications(send, telegram_id: int, full_name: str) -> None:
    """Общая логика для кнопки «📝 Подтверждения» в меню — все анкеты на
    подтверждении, для федерального координатора (временно — руководители
    регионов пока не подтверждают сами, см. _require_reviewer). Раньше была
    ещё и слэш-команда /applications, убрана как избыточная — то же самое
    доступно кнопкой."""
    async with async_session() as session:
        user = await resolve_user(session, telegram_id, full_name)
        if user is None:
            return
        if user.role not in (ROLE_FEDERAL, ROLE_SUPERUSER):
            await send("Доступно только администраторам.")
            return
        stmt = select(MembershipApplication).where(MembershipApplication.state == APPLICATION_STATE_PENDING)
        pending = list((await session.execute(stmt.order_by(MembershipApplication.created_at))).scalars().all())

    if not pending:
        await send("Анкет на подтверждении нет.")
        return
    for application in pending:
        await send(
            await _render_application_text(application),
            reply_markup=_review_keyboard(application.id),
            parse_mode="HTML",
        )


async def list_applications(message: Message) -> None:
    """Список ожидающих анкет своего региона — на случай, если уведомление
    потерялось (бот перезапускался, сообщение прокручено)."""
    await _send_pending_applications(message.answer, message.from_user.id, message.from_user.full_name)


@router.callback_query(F.data == "apply:list")
async def list_applications_button(callback: CallbackQuery) -> None:
    await _send_pending_applications(callback.message.answer, callback.from_user.id, callback.from_user.full_name)
    await callback.answer()


@router.callback_query(F.data == "apply:link")
async def show_application_link(callback: CallbackQuery) -> None:
    async with async_session() as session:
        user = await resolve_user(session, callback.from_user.id, callback.from_user.full_name)
        if user is None:
            await callback.answer("Доступ не открыт", show_alert=True)
            return
        region = (await session.execute(select(Region).where(Region.leader_user_id == user.id))).scalar_one_or_none()
        if region is None:
            await callback.answer("Доступно только руководителю региона", show_alert=True)
            return
        code = region.application_code
        region_name = region.name

    bot_username = (await callback.bot.get_me()).username
    link = application_link(bot_username or "<имя_бота>", code) if code else None
    text = (
        f"🔗 <b>Ссылка-приглашение — {region_name}</b>\n\n"
        + (link if link else "Код ещё не сгенерирован — обновите.")
        + "\n\nПо этой ссылке человек, уже принятый через отбор на сайте, попадёт прямо в форму "
        "создания личного кабинета с уже выбранным отделением. Ссылка многоразовая — "
        "если её кто-то распространил не туда, обновите, старая перестанет работать."
    )
    await callback.message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔄 Обновить ссылку", callback_data="apply:regenerate")]]
        ),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "apply:regenerate")
async def regenerate_application_link(callback: CallbackQuery) -> None:
    async with async_session() as session:
        user = await resolve_user(session, callback.from_user.id, callback.from_user.full_name)
        if user is None:
            await callback.answer("Доступ не открыт", show_alert=True)
            return
        region = (await session.execute(select(Region).where(Region.leader_user_id == user.id))).scalar_one_or_none()
        if region is None:
            await callback.answer("Доступно только руководителю региона", show_alert=True)
            return
        region = await regenerate_application_code(session, region.id)
        region_name = region.name
        code = region.application_code

    bot_username = (await callback.bot.get_me()).username
    link = application_link(bot_username or "<имя_бота>", code)
    await callback.message.edit_text(
        f"🔗 <b>Ссылка-приглашение — {region_name}</b>\n\n{link}\n\n"
        "Старая ссылка больше не работает.",
        parse_mode="HTML",
    )
    await callback.answer("Ссылка обновлена")


@router.callback_query(F.data.startswith("apply:approve:"))
async def approve_application_button(callback: CallbackQuery) -> None:
    application_id = int(callback.data.split(":")[2])
    async with async_session() as session:
        application = await session.get(MembershipApplication, application_id)
        if application is None:
            await callback.answer("Анкета не найдена", show_alert=True)
            return
        reviewer = await _require_reviewer(callback, application)
        if reviewer is None:
            return
        try:
            approved_user = await svc_approve_application(session, application_id, reviewer.id)
        except ValueError as exc:
            await callback.answer(str(exc), show_alert=True)
            return

        await send_cabinet_welcome(approved_user, bot=callback.bot)

    await callback.message.edit_text(f"✅ Подтверждено: {application.full_name}")
    await callback.answer()


@router.callback_query(F.data.startswith("apply:reject:"))
async def reject_application_button(callback: CallbackQuery) -> None:
    application_id = int(callback.data.split(":")[2])
    async with async_session() as session:
        application = await session.get(MembershipApplication, application_id)
        if application is None:
            await callback.answer("Анкета не найдена", show_alert=True)
            return
        reviewer = await _require_reviewer(callback, application)
        if reviewer is None:
            return
        try:
            await svc_reject_application(session, application_id, reviewer.id)
        except ValueError as exc:
            await callback.answer(str(exc), show_alert=True)
            return

        await notify_telegram(
            application.telegram_id,
            "❌ Подтверждение личного кабинета отклонено. Если это ошибка — напишите администратору.",
        )

    await callback.message.edit_text(f"❌ Отклонено: {application.full_name}")
    await callback.answer()


_EDIT_FIELDS = {
    "name": ("ФИО", "full_name"),
    "phone": ("Телефон", "phone"),
    "birth": ("Дата рождения (ДД.ММ.ГГГГ)", "birth_date"),
    "faculty": ("Факультет", "faculty"),
    "workplace": ("Место работы", "workplace"),
}


@router.callback_query(F.data.startswith("apply:edit:") & ~F.data.contains(":field:"))
async def edit_menu(callback: CallbackQuery) -> None:
    application_id = int(callback.data.split(":")[2])
    async with async_session() as session:
        application = await session.get(MembershipApplication, application_id)
        if application is None:
            await callback.answer("Анкета не найдена", show_alert=True)
            return
        if await _require_reviewer(callback, application) is None:
            return

    buttons = [
        [InlineKeyboardButton(text=label, callback_data=f"apply:edit:{application_id}:field:{key}")]
        for key, (label, _) in _EDIT_FIELDS.items()
    ]
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"apply:back:{application_id}")])
    await callback.message.edit_text("Что поправить?", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


@router.callback_query(F.data.startswith("apply:back:"))
async def back_to_review(callback: CallbackQuery) -> None:
    application_id = int(callback.data.split(":")[2])
    async with async_session() as session:
        application = await session.get(MembershipApplication, application_id)
        if application is None:
            await callback.answer("Анкета не найдена", show_alert=True)
            return
        text = await _render_application_text(application)
    await callback.message.edit_text(text, reply_markup=_review_keyboard(application_id), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("apply:edit:") & F.data.contains(":field:"))
async def edit_field_start(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, application_id, _, field = callback.data.split(":")
    application = None
    async with async_session() as session:
        application = await session.get(MembershipApplication, int(application_id))
        if application is None:
            await callback.answer("Анкета не найдена", show_alert=True)
            return
        if await _require_reviewer(callback, application) is None:
            return

    label, _ = _EDIT_FIELDS[field]
    await state.set_state(ApplicationEditStates.waiting_value)
    await state.update_data(application_id=int(application_id), field=field)
    await callback.message.edit_text(f"Новое значение — {label}:")
    await callback.answer()


@router.message(ApplicationEditStates.waiting_value)
async def edit_field_apply(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    field = data["field"]
    raw = (message.text or "").strip()
    await state.clear()

    async with async_session() as session:
        application = await session.get(MembershipApplication, data["application_id"])
        if application is None:
            await message.answer("Анкета не найдена.")
            return

        if field == "birth":
            parsed = parse_date_hint(raw, tz_today())
            if parsed is None:
                await message.answer("Не разобрал дату, попробуйте ещё раз в формате ДД.ММ.ГГГГ.")
                return
            application.birth_date = parsed
        else:
            _, attr = _EDIT_FIELDS[field]
            setattr(application, attr, raw or None)

        await session.commit()
        text = await _render_application_text(application)

    await message.answer(text, reply_markup=_review_keyboard(application.id), parse_mode="HTML")
