"""Задачи мероприятия (раньше «подзадачи») — срок, статус и список
исполнителей на каждую. Выполнение задач не влияет на статус самого
мероприятия: руководитель закрывает его вручную независимо от того, закрыты
ли задачи.

Категории у задачи больше нет. Она заводилась ради радара «к чему тяготеет»
в «Академии», а задачи мероприятий из «Академии» ушли — спрашивать её стало не для
чего, и в форме постановки она была лишним шагом.

Награды за такую задачу нет намеренно. Раньше руководитель выбирал её сам,
1–3 звезды на задачу, — единственное место во всей системе, где число бралось
из головы. Заработок в «Академии» ограничен лесенками заданий (потолок примерно
63 звезды на человека за всё время), а здесь потолка не было: девять задач за
вечер намолотили тринадцать звёзд, пятую часть пожизненного предела. Рядовая
работа отмечается сделанной, а не оплачивается."""

from datetime import date as date_

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user, get_db
from api.serializers import event_task_dict
from database.db import async_session
from database.models import (
    EVENT_STATUS_DONE,
    EVENT_TASK_STATUS_LABELS,
    Event,
    EventTask,
    EventTaskAssignee,
    Member,
    User,
)
from utils.access import actor_cell, require_edit, require_same_cell, require_view
from utils.notify import escape_telegram_html, notify_telegram
from utils.parser import format_date_ru

router = APIRouter(prefix="/events/{event_id}/tasks", tags=["event_tasks"])

class EventTaskIn(BaseModel):
    title: str = Field(min_length=2, max_length=255)
    due_date: date_
    # Один исполнитель на задачу: он же и ответственный за неё. Раньше их могло
    # быть несколько, и было непонятно, с кого спрашивать; в боевой базе,
    # впрочем, ни у одной задачи двоих так и не оказалось.
    assignee_member_id: int | None = None
    position: int = Field(default=0, ge=-2_147_483_648, le=2_147_483_647)


class EventTaskPatch(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=255)
    due_date: date_ | None = None
    status: str | None = Field(default=None, max_length=16)
    assignee_member_id: int | None = None
    position: int | None = Field(default=None, ge=-2_147_483_648, le=2_147_483_647)


async def _get_event(session: AsyncSession, event_id: int) -> Event:
    event = await session.get(Event, event_id)
    if event is None:
        raise HTTPException(404, "Мероприятие не найдено")
    return event


async def _assignee(session: AsyncSession, task_id: int) -> tuple[int | None, str | None]:
    """Исполнитель задачи — один. Отдаём и его id, и имя: список задач иначе
    пришлось бы дополнять вторым запросом на каждой строке."""
    row = (
        await session.execute(
            select(EventTaskAssignee.member_id, Member.full_name)
            .join(Member, Member.id == EventTaskAssignee.member_id)
            .where(EventTaskAssignee.task_id == task_id)
        )
    ).first()
    return (row[0], row[1]) if row else (None, None)


async def _set_assignee(
    session: AsyncSession, task: EventTask, member_id: int | None, region_id: int
) -> int | None:
    """Ставит одного исполнителя. Возвращает его member_id, если он тут впервые,
    — ему и уходит уведомление о новой задаче.

    Строку прежнего исполнителя удаляем, а строку того же самого не трогаем:
    иначе любое сохранение формы сбрасывало бы отметку «прочитано» и снова
    зажигало кружок у человека, который задачу уже видел.
    """
    if member_id is not None:
        valid = (
            await session.execute(
                select(Member.id).where(Member.id == member_id, Member.region_id == region_id)
            )
        ).scalar_one_or_none()
        if valid is None:
            raise HTTPException(400, "Исполнитель должен быть из состава этого региона")

    existing = (
        await session.execute(select(EventTaskAssignee).where(EventTaskAssignee.task_id == task.id))
    ).scalars().all()
    for row in existing:
        if row.member_id != member_id:
            await session.delete(row)
    if member_id is None or any(row.member_id == member_id for row in existing):
        return None
    session.add(EventTaskAssignee(task_id=task.id, member_id=member_id))
    return member_id


async def _send_task_assignment_notifications(
    event_title: str, task_title: str, due_date: date_, member_ids: set[int]
) -> None:
    """Фоновая задача (см. вызовы через BackgroundTasks ниже) — не задерживает
    ответ руководителю и может себе позволить более терпеливые ретраи: разовый
    сбой DNS на сервере при обращении к api.telegram.org (наблюдался в проде)
    бывает длиннее пары секунд. Открывает свою сессию — сессия запроса к
    этому моменту уже закрыта."""
    if not member_ids:
        return
    text = (
        "📋 Вам поставлена новая задача\n\n"
        f"Мероприятие: «{escape_telegram_html(event_title)}»\n"
        f"Задача: «{escape_telegram_html(task_title)}»\n"
        f"Срок: {format_date_ru(due_date)}"
    )
    async with async_session() as session:
        telegram_ids = (
            await session.execute(
                select(User.telegram_id).where(User.member_id.in_(member_ids), User.telegram_id.is_not(None))
            )
        ).scalars().all()
    for telegram_id in telegram_ids:
        await notify_telegram(telegram_id, text, retries=4, retry_delay=5)


def _schedule_assignment_notifications(
    background_tasks: BackgroundTasks, event: Event, task: EventTask, member_id: int | None
) -> None:
    if member_id is None:
        return
    member_ids = {member_id}
    background_tasks.add_task(
        _send_task_assignment_notifications, event.title, task.title, task.due_date, member_ids
    )


async def _send_task_completion_notifications(event_title: str, task_title: str, member_ids: set[int]) -> None:
    """Исполнителю — когда руководитель отмечает его задачу выполненной
    (он сам эту отметку не ставит, у него нет статус-селектора, см.
    webapp/app.js::eventTasksModal)."""
    if not member_ids:
        return
    text = (
        "✅ Задача выполнена\n\n"
        f"«{escape_telegram_html(task_title)}» — {escape_telegram_html(event_title)}\n"
        "Руководитель отметил её выполненной."
    )
    async with async_session() as session:
        telegram_ids = (
            await session.execute(
                select(User.telegram_id).where(User.member_id.in_(member_ids), User.telegram_id.is_not(None))
            )
        ).scalars().all()
    for telegram_id in telegram_ids:
        await notify_telegram(telegram_id, text, retries=4, retry_delay=5)


def _schedule_completion_notifications(
    background_tasks: BackgroundTasks, event: Event, task: EventTask, member_ids: list[int]
) -> None:
    if not member_ids:
        return
    background_tasks.add_task(_send_task_completion_notifications, event.title, task.title, set(member_ids))


@router.get("")
async def list_event_tasks(
    event_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    event = await _get_event(session, event_id)
    await require_view(session, user, event.region_id)
    require_same_cell(await actor_cell(session, user), event.cell_id)

    tasks = (
        await session.execute(select(EventTask).where(EventTask.event_id == event_id).order_by(EventTask.position))
    ).scalars().all()
    items = [event_task_dict(t, *await _assignee(session, t.id)) for t in tasks]
    return {"items": items}


@router.post("")
async def create_event_task(
    event_id: int,
    payload: EventTaskIn,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    event = await _get_event(session, event_id)
    await require_edit(session, user, event.region_id)
    require_same_cell(await actor_cell(session, user), event.cell_id)

    task = EventTask(
        event_id=event_id,
        title=payload.title,
        due_date=payload.due_date,
        position=payload.position,
    )
    session.add(task)
    await session.flush()
    added = await _set_assignee(session, task, payload.assignee_member_id, event.region_id)
    await session.commit()
    await session.refresh(task)
    _schedule_assignment_notifications(background_tasks, event, task, added)
    return event_task_dict(task, *await _assignee(session, task.id))


@router.patch("/{task_id}")
async def update_event_task(
    event_id: int,
    task_id: int,
    payload: EventTaskPatch,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    event = await _get_event(session, event_id)
    # Здесь задачей распоряжается тот, кто её поставил. Исполнитель свой статус
    # ставит из «Задач» (api/routers/tasks.py::set_event_task_status) — там ему
    # и место, а сюда он не ходит.
    await require_edit(session, user, event.region_id)
    require_same_cell(await actor_cell(session, user), event.cell_id)

    task = await session.get(EventTask, task_id)
    if task is None or task.event_id != event_id:
        raise HTTPException(404, "Подзадача не найдена")
    was_done = task.status == EVENT_STATUS_DONE

    data = payload.model_dump(exclude_unset=True)
    if data.get("status") and data["status"] not in EVENT_TASK_STATUS_LABELS:
        raise HTTPException(400, f"Неизвестный статус: {data['status']}")

    assignee_given = "assignee_member_id" in data
    assignee_id = data.pop("assignee_member_id", None)
    for field, value in data.items():
        setattr(task, field, value)
    added = await _set_assignee(session, task, assignee_id, event.region_id) if assignee_given else None

    await session.commit()
    await session.refresh(task)
    _schedule_assignment_notifications(background_tasks, event, task, added)
    if task.status == EVENT_STATUS_DONE and not was_done:
        _schedule_completion_notifications(background_tasks, event, task, [i for i in [(await _assignee(session, task.id))[0]] if i])
    return event_task_dict(task, *await _assignee(session, task.id))


@router.delete("/{task_id}")
async def delete_event_task(
    event_id: int,
    task_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    event = await _get_event(session, event_id)
    await require_edit(session, user, event.region_id)
    require_same_cell(await actor_cell(session, user), event.cell_id)

    task = await session.get(EventTask, task_id)
    if task is None or task.event_id != event_id:
        raise HTTPException(404, "Подзадача не найдена")

    await session.delete(task)
    await session.commit()
    return {"ok": True}
