"""«Задачи» (ТЗ §10.2): постановка, смена статуса, уведомления обеим сторонам."""

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    TASK_STATUS_DONE,
    TASK_STATUS_IN_PROGRESS,
    TASK_STATUS_LABELS,
    TASK_STATUS_NEW,
    TASK_STATUS_OVERDUE,
    TASK_STATUS_REVIEW,
    Task,
    User,
)
from utils.access import AccessDenied, can_message
from utils.notify import escape_telegram_html, notify_telegram
from utils.parser import format_date_ru
from utils.tz import now, today

# Кто какой статус вправе ставить: исполнитель ведёт работу, постановщик — отменяет.
ASSIGNEE_STATUSES = (TASK_STATUS_IN_PROGRESS, TASK_STATUS_REVIEW)
# Постановщик может вернуть задачу в новые. Отменить — нельзя: отменённой
# задачи у нас не бывает, её удаляют (delete_task).
AUTHOR_STATUSES = (TASK_STATUS_NEW, TASK_STATUS_IN_PROGRESS, TASK_STATUS_DONE)


async def create_task(
    session: AsyncSession,
    author: User,
    assignee_id: int,
    title: str,
    text: str | None = None,
    deadline: date | None = None,
) -> Task:
    title = title.strip()
    if not title:
        raise ValueError("Пустая задача")
    if not await can_message(session, author, assignee_id):
        raise AccessDenied("Этому человеку нельзя ставить задачи")

    task = Task(
        from_user_id=author.id,
        to_user_id=assignee_id,
        title=title,
        text=(text or "").strip() or None,
        deadline=deadline,
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)

    assignee = await session.get(User, assignee_id)
    deadline_line = f"\nСрок: <b>{format_date_ru(deadline)}</b>" if deadline else ""
    body = f"\n\n{escape_telegram_html(task.text)}" if task.text else ""
    if assignee is not None:
        await notify_telegram(
            assignee.telegram_id,
            f"✅ <b>Новая задача</b>\nОт: {escape_telegram_html(author.full_name)}{deadline_line}\n\n"
            f"<b>{escape_telegram_html(task.title)}</b>{body}",
        )
    return task


async def change_status(session: AsyncSession, task: Task, actor: User, new_status: str) -> Task:
    """Меняет статус и уведомляет вторую сторону (ТЗ §14: «задача изменила статус» → постановщику)."""
    if new_status not in TASK_STATUS_LABELS:
        raise ValueError(f"Неизвестный статус: {new_status}")

    is_assignee = task.to_user_id == actor.id
    is_author = task.from_user_id == actor.id
    if not (is_assignee or is_author):
        raise AccessDenied("Задача не ваша")
    if is_assignee and not is_author and new_status not in ASSIGNEE_STATUSES:
        raise AccessDenied("Исполнитель может взять задачу в работу или отправить на проверку")
    if is_author and not is_assignee and new_status not in AUTHOR_STATUSES:
        raise AccessDenied("Постановщик проверяет выполнение или возвращает задачу в работу")
    if is_author and not is_assignee and new_status == TASK_STATUS_DONE and task.status != TASK_STATUS_REVIEW:
        raise AccessDenied("Сначала исполнитель должен отправить задачу на проверку")

    if task.status == new_status:
        return task

    task.status = new_status
    task.updated_at = now().replace(tzinfo=None)
    if new_status == TASK_STATUS_REVIEW:
        task.submitted_at = task.updated_at
        task.reviewed_at = None
        task.review_note = None
    if new_status == TASK_STATUS_DONE:
        task.overdue_notified = False
        task.reviewed_at = task.updated_at
    if is_author and new_status in (TASK_STATUS_NEW, TASK_STATUS_IN_PROGRESS):
        task.reviewed_at = task.updated_at
    await session.commit()
    await session.refresh(task)

    counterpart_id = task.from_user_id if is_assignee else task.to_user_id
    counterpart = await session.get(User, counterpart_id)
    if counterpart is not None and counterpart_id != actor.id:
        await notify_telegram(
            counterpart.telegram_id,
            f"🔄 <b>Задача «{escape_telegram_html(task.title)}»</b>\n"
            f"{escape_telegram_html(actor.full_name)} → статус: "
            f"<b>{escape_telegram_html(TASK_STATUS_LABELS[new_status])}</b>",
        )
    return task


async def delete_task(session: AsyncSession, task: Task, actor: User) -> None:
    """Постановщик снимает задачу — она исчезает совсем.

    Раньше она получала статус «Отменена» и оставалась висеть в ящике у
    исполнителя: работы по ней нет, а строка есть, и её ни закрыть, ни убрать.
    Отменённая задача — это не состояние работы, а её отсутствие, и держать
    для неё место в списке незачем.

    Снимает только постановщик: исполнитель, которому задача не нравится,
    отчитывается по ней, а не стирает её.
    """
    if task.from_user_id != actor.id:
        raise AccessDenied("Снять задачу может тот, кто её поставил")

    assignee_id, title = task.to_user_id, task.title
    await session.delete(task)
    await session.commit()

    assignee = await session.get(User, assignee_id)
    if assignee is not None and assignee_id != actor.id:
        await notify_telegram(
            assignee.telegram_id,
            f"🗑 <b>Задача снята</b>\n{escape_telegram_html(actor.full_name)} "
            f"убрал(а) задачу «{escape_telegram_html(title)}»",
        )


async def mark_read(session: AsyncSession, task: Task, reader_id: int) -> None:
    if task.to_user_id != reader_id or task.is_read:
        return
    task.is_read = True
    await session.commit()


def is_overdue(task: Task) -> bool:
    return (
        task.deadline is not None
        and task.status in (TASK_STATUS_NEW, TASK_STATUS_IN_PROGRESS)
        and task.deadline < today()
    )


def effective_status(task: Task) -> str:
    """Статус для показа: просрочка вычисляется от дедлайна, а не хранится вручную."""
    return TASK_STATUS_OVERDUE if is_overdue(task) else task.status
