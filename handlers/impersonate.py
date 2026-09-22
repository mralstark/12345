"""«Войти как» — диагностика кабинета глазами другого пользователя: от
обычного участника (активист/член Братства/выпускник — по статусу в
«Составе», см. GROUPS ниже) до любой управленческой роли.

Кнопка доступна только техническому superuser: если вернуть её федеральному,
resolve_user подставит роль выбранной цели и тем самым повысит полномочия.
API дополнительно запрещает любые изменения в этом режиме."""

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from database.db import async_session
from database.models import (
    MEMBER_STATUS_ACTIVIST,
    MEMBER_STATUS_ALUMNI,
    MEMBER_STATUS_LABELS,
    MEMBER_STATUS_MEMBER,
    ROLE_CELL_LEADER,
    ROLE_COORDINATOR,
    ROLE_FEDERAL,
    ROLE_LABELS,
    ROLE_LEADER,
    User,
)
from handlers.common import show_main_menu
from utils.permissions import has_role
from utils.users import (
    IMPERSONATOR_ROLES,
    candidate_context,
    get_user_by_telegram_id,
    impersonation_candidates,
    resolve_user,
    start_impersonation,
    stop_impersonation,
)

router = Router()

# Порядок и подписи в пикере «Войти как...» — по просьбе: сначала обычные
# участники по статусу в составе (не роль — у них у всех role=participant,
# см. utils.users.impersonation_candidates), потом управленческие роли снизу
# вверх. group-ключ — либо «status:<статус>» (Member.status), либо голая
# роль (User.role), см. impersonation_candidates.
GROUPS = (
    (f"status:{MEMBER_STATUS_ACTIVIST}", MEMBER_STATUS_LABELS[MEMBER_STATUS_ACTIVIST]),
    (f"status:{MEMBER_STATUS_MEMBER}", MEMBER_STATUS_LABELS[MEMBER_STATUS_MEMBER]),
    (f"status:{MEMBER_STATUS_ALUMNI}", MEMBER_STATUS_LABELS[MEMBER_STATUS_ALUMNI]),
    (ROLE_CELL_LEADER, ROLE_LABELS[ROLE_CELL_LEADER]),
    (ROLE_LEADER, ROLE_LABELS[ROLE_LEADER]),
    (ROLE_COORDINATOR, ROLE_LABELS[ROLE_COORDINATOR]),
    (ROLE_FEDERAL, ROLE_LABELS[ROLE_FEDERAL]),
)
GROUP_LABELS = dict(GROUPS)


async def _require_admin(callback: CallbackQuery, session) -> User | None:
    real = await get_user_by_telegram_id(session, callback.from_user.id)
    if real is None or not has_role(real, "superuser"):
        await callback.answer("Доступно только администраторам", show_alert=True)
        return None
    return real


@router.callback_query(F.data == "menu:impersonate")
async def pick_role(callback: CallbackQuery) -> None:
    async with async_session() as session:
        if await _require_admin(callback, session) is None:
            return

        buttons = [
            [InlineKeyboardButton(text=label, callback_data=f"impersonate:role:{group}")]
            for group, label in GROUPS
        ]
        buttons.append([InlineKeyboardButton(text="⬅️ Отмена", callback_data="menu:main")])
        await callback.message.edit_text(
            "🕵 <b>Войти как...</b>\n\nВыберите группу, затем конкретного человека.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode="HTML",
        )
        await callback.answer()


@router.callback_query(F.data.startswith("impersonate:role:"))
async def pick_person(callback: CallbackQuery) -> None:
    group = callback.data.split(":", 2)[2]
    async with async_session() as session:
        if await _require_admin(callback, session) is None:
            return

        candidates = await impersonation_candidates(session, group)
        if not candidates:
            await callback.answer("В этой группе пока никого нет", show_alert=True)
            return

        buttons = []
        for person in candidates:
            context = await candidate_context(session, person)
            label = f"{person.full_name} — {context}"[:64]
            buttons.append([InlineKeyboardButton(text=label, callback_data=f"impersonate:pick:{person.id}")])
        buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:impersonate")])
        await callback.message.edit_text(
            f"🕵 <b>{GROUP_LABELS[group]}</b>\n\nКого показать?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode="HTML",
        )
        await callback.answer()


@router.callback_query(F.data.startswith("impersonate:pick:"))
async def activate(callback: CallbackQuery) -> None:
    target_id = int(callback.data.split(":")[2])
    async with async_session() as session:
        real = await _require_admin(callback, session)
        if real is None:
            return
        try:
            await start_impersonation(session, real, target_id)
        except ValueError as exc:
            await callback.answer(str(exc), show_alert=True)
            return

        user = await resolve_user(session, callback.from_user.id, callback.from_user.full_name)
        await callback.answer(f"Теперь вы смотрите как {user.full_name}")
        await show_main_menu(callback, session, user)


@router.callback_query(F.data == "menu:stop_impersonate")
async def deactivate(callback: CallbackQuery) -> None:
    async with async_session() as session:
        real = await _require_admin(callback, session)
        if real is None:
            return
        await stop_impersonation(session, real)
        user = await resolve_user(session, callback.from_user.id, callback.from_user.full_name)
        await callback.answer("Вернулись в свой аккаунт")
        await show_main_menu(callback, session, user)
