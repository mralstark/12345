"""Общее для всех хендлеров бота: получение пользователя и отрисовка главного меню."""

from aiogram.types import CallbackQuery
from aiogram.types import Message as TgMessage
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import ROLE_LEADER, SUPERVISOR_ROLES, User
from keyboards.main_menu import main_menu_keyboard
from utils.access import accessible_regions
from utils.counters import (
    new_tasks_count,
    pending_applications_count,
)
from utils.notify import escape_telegram_html
from utils.roles import role_label

NO_ACCESS_TEXT = (
    "🚫 <b>Доступ не открыт</b>\n\n"
    "У вас ещё нет личного кабинета. Наберите /start — там будут две кнопки: "
    "подать заявку на сайте (если вы ещё не проходили отбор) и создать личный "
    "кабинет (если уже приняты)."
)

PENDING_APPLICATION_TEXT = "Ваша заявка уже обрабатывается, пожалуйста, дождитесь решения."


async def menu_text(session: AsyncSession, user: User) -> str:
    regions = await accessible_regions(session, user)
    tasks = await new_tasks_count(session, user.id)
    applications = await pending_applications_count(session, user)

    lines = ["🏛 <b>Личный кабинет Братства Академистов</b>", ""]

    impersonated_by = getattr(user, "_impersonated_by", None)
    if impersonated_by is not None:
        lines.append(f"👁 Вы ({escape_telegram_html(impersonated_by.full_name)}) смотрите как:")

    lines.append(escape_telegram_html(user.full_name))
    # У участника роли нет — role_label вернёт None, и строки не будет вовсе.
    role = await role_label(session, user)
    if role:
        lines.append(f"Роль: {escape_telegram_html(role)}")

    if user.role == ROLE_LEADER:
        region_name = regions[0].name if regions else "— не назначен —"
        lines.append(f"Регион: {escape_telegram_html(region_name)}")
    elif user.role in SUPERVISOR_ROLES:
        lines.append(f"Регионов в ведении: {len(regions)}")

    badges = []
    if tasks:
        badges.append(f"✅ новых задач: {tasks}")
    if applications:
        badges.append(f"📝 анкет на подтверждении: {applications}")
    if badges:
        lines.append("")
        lines.extend(badges)

    return "\n".join(lines)


async def show_main_menu(event: TgMessage | CallbackQuery, session: AsyncSession, user: User) -> None:
    text = await menu_text(session, user)
    applications = await pending_applications_count(session, user)
    keyboard = main_menu_keyboard(user, pending_applications=applications)

    if isinstance(event, CallbackQuery):
        if event.message is not None:
            await event.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        await event.answer()
    else:
        await event.answer(text, reply_markup=keyboard, parse_mode="HTML")
