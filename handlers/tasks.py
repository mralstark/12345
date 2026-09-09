"""«Задачи» в боте: входящие, поставленные мной, быстрая смена статуса (ТЗ §10.2)."""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from database.db import async_session
from database.models import (
    TASK_STATUS_DONE,
    TASK_STATUS_IN_PROGRESS,
    TASK_STATUS_LABELS,
    Task,
    User,
)
from handlers.common import NO_ACCESS_TEXT, show_main_menu
from keyboards.main_menu import back_to_menu_keyboard
from services.tasks import change_status, create_task, delete_task, effective_status, mark_read
from utils.access import AccessDenied, correspondents
from utils.parser import format_date_ru, parse_deadline
from utils.tz import today
from utils.users import resolve_user

router = Router()

PAGE_SIZE = 8

STATUS_ICONS = {
    "new": "🆕",
    "in_progress": "⏳",
    "done": "✅",
    "overdue": "🔴",
    "cancelled": "🚫",
}


class TaskStates(StatesGroup):
    waiting_title = State()
    waiting_deadline = State()


def _tabs_row(active: str) -> list[InlineKeyboardButton]:
    inbox = "📥 Мои задачи" + (" •" if active == "inbox" else "")
    outbox = "📤 Поставленные" + (" •" if active == "outbox" else "")
    return [
        InlineKeyboardButton(text=inbox, callback_data="task:inbox:0"),
        InlineKeyboardButton(text=outbox, callback_data="task:outbox:0"),
    ]


async def _render_list(callback: CallbackQuery, tab: str, offset: int) -> None:
    async with async_session() as session:
        user = await resolve_user(session, callback.from_user.id, callback.from_user.full_name)
        if user is None:
            await callback.answer("Доступ не открыт", show_alert=True)
            return

        column = Task.to_user_id if tab == "inbox" else Task.from_user_id
        counterpart = Task.from_user_id if tab == "inbox" else Task.to_user_id
        result = await session.execute(
            select(Task, User)
            .join(User, User.id == counterpart)
            .where(column == user.id)
            .order_by(Task.created_at.desc(), Task.id.desc())
            .offset(offset)
            .limit(PAGE_SIZE + 1)
        )
        rows = result.all()

    has_more = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]

    buttons: list[list[InlineKeyboardButton]] = [_tabs_row(tab)]
    for task, person in rows:
        icon = STATUS_ICONS.get(effective_status(task), "•")
        unread = "🔵 " if tab == "inbox" and not task.is_read else ""
        deadline = f" · до {task.deadline.strftime('%d.%m')}" if task.deadline else ""
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"{unread}{icon} {task.title[:32]}{deadline}",
                    callback_data=f"task:open:{task.id}:{tab}",
                )
            ]
        )

    nav: list[InlineKeyboardButton] = []
    if offset:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"task:{tab}:{max(offset - PAGE_SIZE, 0)}"))
    if has_more:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"task:{tab}:{offset + PAGE_SIZE}"))
    if nav:
        buttons.append(nav)

    buttons.append([InlineKeyboardButton(text="➕ Поставить задачу", callback_data="task:new")])
    buttons.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:main")])

    header = "✅ <b>Мои задачи</b>" if tab == "inbox" else "✅ <b>Поставленные мной</b>"
    if not rows:
        header += "\n\nПока пусто."

    await callback.message.edit_text(
        header, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("task:inbox:"))
async def tasks_inbox(callback: CallbackQuery) -> None:
    await _render_list(callback, "inbox", int(callback.data.split(":")[2]))


@router.callback_query(F.data.startswith("task:outbox:"))
async def tasks_outbox(callback: CallbackQuery) -> None:
    await _render_list(callback, "outbox", int(callback.data.split(":")[2]))


@router.callback_query(F.data.startswith("task:open:"))
async def task_open(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    task_id, tab = int(parts[2]), parts[3]

    async with async_session() as session:
        user = await resolve_user(session, callback.from_user.id, callback.from_user.full_name)
        if user is None:
            await callback.answer("Доступ не открыт", show_alert=True)
            return

        task = await session.get(Task, task_id)
        if task is None or user.id not in (task.to_user_id, task.from_user_id):
            await callback.answer("Задача не найдена", show_alert=True)
            return

        await mark_read(session, task, user.id)
        author = await session.get(User, task.from_user_id)
        assignee = await session.get(User, task.to_user_id)

    status = effective_status(task)
    lines = [
        f"{STATUS_ICONS.get(status, '•')} <b>{task.title}</b>",
        "",
        f"Постановщик: {author.full_name if author else '—'}",
        f"Исполнитель: {assignee.full_name if assignee else '—'}",
        f"Статус: <b>{TASK_STATUS_LABELS[status]}</b>",
    ]
    if task.deadline:
        lines.append(f"Срок: {format_date_ru(task.deadline)}")
    if task.text:
        lines.extend(["", task.text])

    buttons: list[list[InlineKeyboardButton]] = []
    if task.to_user_id == user.id and task.status in ("new", "in_progress"):
        row = []
        if task.status == "new":
            row.append(
                InlineKeyboardButton(text="⏳ В работу", callback_data=f"task:set:{task.id}:{TASK_STATUS_IN_PROGRESS}:{tab}")
            )
        row.append(
            InlineKeyboardButton(text="✅ Выполнена", callback_data=f"task:set:{task.id}:{TASK_STATUS_DONE}:{tab}")
        )
        buttons.append(row)
    # Снять задачу — значит убрать её совсем: отменённых задач у нас нет,
    # они не оставляют после себя строку в чужом ящике.
    if task.from_user_id == user.id and task.status in ("new", "in_progress"):
        buttons.append(
            [InlineKeyboardButton(text="🗑 Снять задачу", callback_data=f"task:del:{task.id}:{tab}")]
        )
    buttons.append([InlineKeyboardButton(text="⬅️ К списку", callback_data=f"task:{tab}:0")])

    await callback.message.edit_text(
        "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("task:del:"))
async def task_delete(callback: CallbackQuery) -> None:
    """Постановщик снимает задачу — она исчезает и у исполнителя тоже."""
    parts = callback.data.split(":")
    task_id, tab = int(parts[2]), parts[3]

    async with async_session() as session:
        user = await resolve_user(session, callback.from_user.id, callback.from_user.full_name)
        if user is None:
            await callback.answer("Доступ не открыт", show_alert=True)
            return
        task = await session.get(Task, task_id)
        if task is None:
            await callback.answer("Задача не найдена", show_alert=True)
            return
        try:
            await delete_task(session, task, user)
        except AccessDenied as exc:
            await callback.answer(str(exc), show_alert=True)
            return

    await callback.answer("Задача снята")
    await _render_list(callback, tab, 0)


@router.callback_query(F.data.startswith("task:set:"))
async def task_set_status(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    task_id, new_status, tab = int(parts[2]), parts[3], parts[4]

    async with async_session() as session:
        user = await resolve_user(session, callback.from_user.id, callback.from_user.full_name)
        if user is None:
            await callback.answer("Доступ не открыт", show_alert=True)
            return
        task = await session.get(Task, task_id)
        if task is None:
            await callback.answer("Задача не найдена", show_alert=True)
            return
        try:
            await change_status(session, task, user, new_status)
        except (AccessDenied, ValueError) as exc:
            await callback.answer(str(exc), show_alert=True)
            return

    callback.data = f"task:open:{task_id}:{tab}"
    await task_open(callback)


@router.callback_query(F.data == "task:new")
async def task_new(callback: CallbackQuery) -> None:
    async with async_session() as session:
        user = await resolve_user(session, callback.from_user.id, callback.from_user.full_name)
        if user is None:
            await callback.answer("Доступ не открыт", show_alert=True)
            return
        people = await correspondents(session, user)

    if not people:
        await callback.answer("Нет доступных исполнителей", show_alert=True)
        return

    buttons = [
        [InlineKeyboardButton(text=person.full_name, callback_data=f"task:to:{person.id}")]
        for person in people[:20]
    ]
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="task:inbox:0")])
    await callback.message.edit_text(
        "➕ <b>Кому поставить задачу?</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("task:to:"))
async def task_pick_assignee(callback: CallbackQuery, state: FSMContext) -> None:
    assignee_id = int(callback.data.split(":")[2])
    await state.set_state(TaskStates.waiting_title)
    await state.update_data(assignee_id=assignee_id)
    await callback.message.edit_text(
        "➕ Опишите задачу одним сообщением.\n"
        "Первая строка — название, остальное (необязательно) — подробности.\n\n"
        "/cancel — отмена.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(TaskStates.waiting_title, Command("cancel"))
@router.message(TaskStates.waiting_deadline, Command("cancel"))
async def task_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    async with async_session() as session:
        user = await resolve_user(session, message.from_user.id, message.from_user.full_name)
        if user is None:
            await message.answer(NO_ACCESS_TEXT, parse_mode="HTML")
            return
        await show_main_menu(message, session, user)


@router.message(TaskStates.waiting_title)
async def task_take_title(message: Message, state: FSMContext) -> None:
    lines = (message.text or "").strip().splitlines()
    if not lines or not lines[0].strip():
        await message.answer("Название пустое — напишите текст задачи.")
        return

    await state.update_data(title=lines[0].strip()[:255], body="\n".join(lines[1:]).strip())
    await state.set_state(TaskStates.waiting_deadline)
    await message.answer(
        "🗓 Укажите срок: <code>31.12</code>, <code>31.12.2026</code> или <code>завтра</code>.\n"
        "Отправьте <code>-</code>, если срока нет.",
        parse_mode="HTML",
    )


@router.message(TaskStates.waiting_deadline)
async def task_take_deadline(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    deadline = None
    if raw not in ("-", "—", "нет"):
        deadline = parse_deadline(raw, today())
        if deadline is None:
            await message.answer("Не понял дату. Формат: 31.12 или 31.12.2026, либо «-» без срока.")
            return

    data = await state.get_data()
    await state.clear()

    async with async_session() as session:
        user = await resolve_user(session, message.from_user.id, message.from_user.full_name)
        if user is None:
            await message.answer(NO_ACCESS_TEXT, parse_mode="HTML")
            return
        try:
            await create_task(
                session,
                user,
                int(data["assignee_id"]),
                data["title"],
                data.get("body"),
                deadline,
            )
        except (AccessDenied, ValueError) as exc:
            await message.answer(f"🚫 {exc}", reply_markup=back_to_menu_keyboard())
            return

    deadline_text = f" (срок: {format_date_ru(deadline)})" if deadline else ""
    await message.answer(f"✅ Задача поставлена{deadline_text}.", reply_markup=back_to_menu_keyboard())
