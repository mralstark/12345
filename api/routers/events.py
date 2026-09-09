"""Модуль «Мероприятия» (ТЗ §8): CRUD, повторы, план/факт бюджета, участники."""

from datetime import date as date_
from datetime import time as time_

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user, get_db
from api.serializers import event_dict, event_public_dict, event_task_dict, member_dict
from database.models import (
    EVENT_STATUS_LABELS,
    EVENT_STATUS_PLANNED,
    Event,
    EventAttendance,
    EventTask,
    EventTaskAssignee,
    Member,
    Region,
    Transaction,
    UniversityCell,
    User,
)
from services.recurring_events import RECURRENCE_RULES, materialize_event
from utils.access import actor_cell, require_edit, require_same_cell, require_view
from utils.balance_calc import get_event_fact, get_event_income
from utils.notify import notify_telegram
from utils.parser import format_date_ru
from utils.tz import today as tz_today

router = APIRouter(prefix="/events", tags=["events"])


class EventIn(BaseModel):
    region_id: int
    cell_id: int | None = None
    title: str = Field(min_length=2, max_length=128)
    date: date_
    time: time_ | None = None
    description: str = Field(min_length=1)
    responsible_member_id: int | None = None
    status: str = "planned"
    planned_budget: int | None = Field(default=None, ge=0, description="Копейки")
    is_recurring: bool = False
    recurrence_rule: str | None = None
    recurrence_until: date_ | None = None


class EventPatch(BaseModel):
    cell_id: int | None = None
    title: str | None = Field(default=None, min_length=2, max_length=128)
    date: date_ | None = None
    time: time_ | None = None
    description: str | None = None
    responsible_member_id: int | None = None
    status: str | None = None
    planned_budget: int | None = Field(default=None, ge=0)
    is_recurring: bool | None = None
    recurrence_rule: str | None = None
    recurrence_until: date_ | None = None


class AttendanceIn(BaseModel):
    member_ids: list[int]


class RsvpIn(BaseModel):
    going: bool | None


def _validate_recurrence(is_recurring: bool, rule: str | None) -> None:
    if is_recurring and rule not in RECURRENCE_RULES:
        raise HTTPException(400, f"Правило повтора — одно из: {', '.join(RECURRENCE_RULES)}")


async def _validate_responsible(session: AsyncSession, member_id: int | None, region_id: int) -> None:
    if member_id is None:
        return
    member = await session.get(Member, member_id)
    if member is None or member.region_id != region_id:
        raise HTTPException(400, "Ответственный должен быть из состава этого региона")


async def _own_member(user: User, session: AsyncSession) -> Member:
    if user.member_id is None:
        raise HTTPException(404, "У этого аккаунта нет личного кабинета — он не привязан к «Составу»")
    member = await session.get(Member, user.member_id)
    if member is None:
        raise HTTPException(404, "Запись в составе не найдена")
    return member


def _scope_filter(stmt, scope: str, today: date_):
    """scope: upcoming — от сегодня вперёд, past — прошедшие, all — все."""
    if scope == "upcoming":
        return stmt.where(Event.date >= today).order_by(Event.date)
    if scope == "past":
        return stmt.where(Event.date < today).order_by(Event.date.desc())
    return stmt.order_by(Event.date.desc())


@router.get("")
async def list_events(
    region_id: int,
    scope: str = "upcoming",
    status: str | None = None,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    await require_view(session, user, region_id)

    stmt = select(Event, Member.full_name).join(
        Member, Member.id == Event.responsible_member_id, isouter=True
    ).where(Event.region_id == region_id)
    cell = await actor_cell(session, user)
    if cell is not None:
        stmt = stmt.where(Event.cell_id == cell.id)

    stmt = _scope_filter(stmt, scope, tz_today())
    if status:
        stmt = stmt.where(Event.status == status)

    result = await session.execute(stmt.limit(200))
    items = []
    for event, responsible in result.all():
        fact = await get_event_fact(session, event.id) if event.planned_budget else None
        items.append(event_dict(event, responsible_name=responsible, fact=fact))

    return {"items": items, "statuses": [{"value": k, "label": v} for k, v in EVENT_STATUS_LABELS.items()]}


@router.get("/mine")
async def list_my_events(
    scope: str = "upcoming",
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Календарь мероприятий своего региона глазами обычного участника —
    без бюджета, только для просмотра + своя отметка «иду». Регистрируется
    выше GET /{event_id}, иначе Starlette попробует привести "mine" к int."""
    member = await _own_member(user, session)

    stmt = select(Event).where(Event.region_id == member.region_id)
    stmt = _scope_filter(stmt, scope, tz_today())
    events = list((await session.execute(stmt.limit(200))).scalars().all())

    going_result = await session.execute(
        select(EventAttendance.event_id, EventAttendance.attended).where(
            EventAttendance.member_id == member.id,
            EventAttendance.event_id.in_([e.id for e in events]),
        )
    )
    going_by_event = dict(going_result.all())

    # Кто проводит и с кем связаться (план: показывать это и участнику, не
    # только руководителю) — тот же набор данных, что и в event_dict/get_event.
    region = await session.get(Region, member.region_id)
    cell_ids = {e.cell_id for e in events if e.cell_id is not None}
    cells_by_id = {}
    if cell_ids:
        cells_by_id = {
            c.id: c.name
            for c in (await session.execute(select(UniversityCell).where(UniversityCell.id.in_(cell_ids)))).scalars().all()
        }
    responsible_ids = {e.responsible_member_id for e in events if e.responsible_member_id is not None}
    responsible_by_id: dict[int, Member] = {}
    if responsible_ids:
        responsible_by_id = {
            m.id: m
            for m in (await session.execute(select(Member).where(Member.id.in_(responsible_ids)))).scalars().all()
        }

    items = []
    for e in events:
        responsible = responsible_by_id.get(e.responsible_member_id) if e.responsible_member_id else None
        items.append(
            event_public_dict(
                e,
                going_by_event.get(e.id),
                region_name=region.name if region else None,
                cell_name=cells_by_id.get(e.cell_id),
                responsible_name=responsible.full_name if responsible else None,
                responsible_phone=responsible.phone if responsible else None,
                responsible_telegram=responsible.telegram_username if responsible else None,
            )
        )
    return {"items": items}


@router.get("/{event_id}")
async def get_event(
    event_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    event = await session.get(Event, event_id)
    if event is None:
        raise HTTPException(404, "Мероприятие не найдено")
    await require_view(session, user, event.region_id)
    cell = await actor_cell(session, user)
    require_same_cell(cell, event.cell_id)

    responsible = await session.get(Member, event.responsible_member_id) if event.responsible_member_id else None
    region = await session.get(Region, event.region_id)
    org_cell = await session.get(UniversityCell, event.cell_id) if event.cell_id else None
    fact = await get_event_fact(session, event.id)
    income = await get_event_income(session, event.id)

    attendance_result = await session.execute(
        select(EventAttendance.member_id).where(
            EventAttendance.event_id == event.id, EventAttendance.attended.is_(True)
        )
    )
    attended_ids = list(attendance_result.scalars().all())

    members_stmt = select(Member).where(Member.region_id == event.region_id, Member.is_active.is_(True))
    if cell is not None:
        members_stmt = members_stmt.where(Member.cell_id == cell.id)
    members_result = await session.execute(members_stmt.order_by(Member.full_name))
    members = list(members_result.scalars().all())

    tx_result = await session.execute(
        select(Transaction).where(Transaction.event_id == event.id).order_by(Transaction.date.desc())
    )

    tasks_result = await session.execute(
        select(EventTask).where(EventTask.event_id == event.id).order_by(EventTask.position)
    )
    tasks = tasks_result.scalars().all()
    # Исполнитель у задачи один, он же ответственный: берём его вместе с именем,
    # чтобы карточка мероприятия не ходила за ним отдельно на каждой строке.
    assignees_result = await session.execute(
        select(EventTaskAssignee.task_id, EventTaskAssignee.member_id, Member.full_name)
        .join(Member, Member.id == EventTaskAssignee.member_id)
        .where(EventTaskAssignee.task_id.in_([t.id for t in tasks]))
    )
    assignee_by_task: dict[int, tuple[int, str]] = {
        task_id: (member_id, name) for task_id, member_id, name in assignees_result.all()
    }

    data = event_dict(
        event,
        responsible_name=responsible.full_name if responsible else None,
        fact=fact,
        region_name=region.name if region else None,
        cell_name=org_cell.name if org_cell else None,
        responsible_phone=responsible.phone if responsible else None,
        responsible_telegram=responsible.telegram_username if responsible else None,
    )
    data.update(
        {
            "fact_income": income,
            "budget_diff": (event.planned_budget - fact) if event.planned_budget is not None else None,
            "attended_member_ids": attended_ids,
            "members": [member_dict(m) for m in members],
            "tasks": [event_task_dict(t, *assignee_by_task.get(t.id, (None, None))) for t in tasks],
            "transactions": [
                {
                    "id": tx.id,
                    "amount": tx.amount,
                    "type": tx.type,
                    "date": tx.date.isoformat(),
                    "comment": tx.comment,
                }
                for tx in tx_result.scalars().all()
            ],
        }
    )
    return data


@router.post("")
async def create_event(
    payload: EventIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    await require_edit(session, user, payload.region_id)
    if not payload.description.strip():
        raise HTTPException(400, "Укажите описание")
    _validate_recurrence(payload.is_recurring, payload.recurrence_rule)
    await _validate_responsible(session, payload.responsible_member_id, payload.region_id)
    if payload.status not in EVENT_STATUS_LABELS:
        raise HTTPException(400, f"Неизвестный статус: {payload.status}")

    cell = await actor_cell(session, user)
    payload_data = payload.model_dump()
    if cell is not None:
        # Сервер не доверяет cell_id от клиента, если пишет руководитель ячейки.
        payload_data["cell_id"] = cell.id
    event = Event(**payload_data)
    session.add(event)
    await session.commit()
    await session.refresh(event)

    if event.is_recurring:
        await materialize_event(session, event)

    return event_dict(event)


@router.patch("/{event_id}")
async def update_event(
    event_id: int,
    payload: EventPatch,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    event = await session.get(Event, event_id)
    if event is None:
        raise HTTPException(404, "Мероприятие не найдено")
    await require_edit(session, user, event.region_id)
    cell = await actor_cell(session, user)
    require_same_cell(cell, event.cell_id)

    data = payload.model_dump(exclude_unset=True)
    if cell is not None:
        data.pop("cell_id", None)
    if "description" in data and not (data["description"] or "").strip():
        raise HTTPException(400, "Укажите описание")
    if data.get("status") and data["status"] not in EVENT_STATUS_LABELS:
        raise HTTPException(400, f"Неизвестный статус: {data['status']}")
    if "responsible_member_id" in data:
        await _validate_responsible(session, data["responsible_member_id"], event.region_id)

    for field, value in data.items():
        setattr(event, field, value)
    _validate_recurrence(event.is_recurring, event.recurrence_rule)

    await session.commit()
    await session.refresh(event)

    if event.is_recurring:
        await materialize_event(session, event)

    return event_dict(event, fact=await get_event_fact(session, event.id))


@router.delete("/{event_id}")
async def delete_event(
    event_id: int,
    with_series: bool = False,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    event = await session.get(Event, event_id)
    if event is None:
        raise HTTPException(404, "Мероприятие не найдено")
    await require_edit(session, user, event.region_id)
    require_same_cell(await actor_cell(session, user), event.cell_id)

    # Операции, помеченные мероприятием, остаются в финансах — просто теряют метку.
    tx_result = await session.execute(select(Transaction).where(Transaction.event_id == event.id))
    for transaction in tx_result.scalars().all():
        transaction.event_id = None

    if with_series and event.is_recurring:
        children = await session.execute(select(Event).where(Event.recurrence_parent_id == event.id))
        for child in children.scalars().all():
            child_tx = await session.execute(select(Transaction).where(Transaction.event_id == child.id))
            for transaction in child_tx.scalars().all():
                transaction.event_id = None
            await session.delete(child)
    else:
        # Одиночное удаление шаблона не должно осиротить порождённые записи.
        children = await session.execute(select(Event).where(Event.recurrence_parent_id == event.id))
        for child in children.scalars().all():
            child.recurrence_parent_id = None

    await session.delete(event)
    await session.commit()
    return {"ok": True}


@router.put("/{event_id}/attendance")
async def set_attendance(
    event_id: int,
    payload: AttendanceIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Чек-лист участников: приходит полный список отмеченных (ТЗ §8)."""
    event = await session.get(Event, event_id)
    if event is None:
        raise HTTPException(404, "Мероприятие не найдено")
    await require_edit(session, user, event.region_id)
    cell = await actor_cell(session, user)
    require_same_cell(cell, event.cell_id)

    members_stmt = select(Member.id).where(Member.region_id == event.region_id, Member.is_active.is_(True))
    if cell is not None:
        members_stmt = members_stmt.where(Member.cell_id == cell.id)
    members_result = await session.execute(members_stmt)
    allowed = set(members_result.scalars().all())
    chosen = {member_id for member_id in payload.member_ids if member_id in allowed}

    existing_result = await session.execute(
        select(EventAttendance).where(EventAttendance.event_id == event.id)
    )
    existing = {row.member_id: row for row in existing_result.scalars().all()}

    for member_id in chosen - set(existing):
        session.add(EventAttendance(event_id=event.id, member_id=member_id, attended=True))
    for member_id, row in existing.items():
        row.attended = member_id in chosen

    await session.commit()
    return {"ok": True, "attended": sorted(chosen)}


@router.put("/{event_id}/rsvp")
async def set_rsvp(
    event_id: int,
    payload: RsvpIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Самостоятельная отметка участника «иду/не иду» — пишет в ту же
    EventAttendance, что и чек-лист руководителя (см. set_attendance выше):
    отсутствие строки значит «не отвечал», True/False — «иду»/«не иду». Если
    участник отметился заранее, attendanceModal руководителя уже покажет его
    предзаполненным."""
    member = await _own_member(user, session)
    event = await session.get(Event, event_id)
    if event is None:
        raise HTTPException(404, "Мероприятие не найдено")
    if event.region_id != member.region_id:
        raise HTTPException(403, "Мероприятие другого региона")
    if event.status != EVENT_STATUS_PLANNED:
        raise HTTPException(400, "Отметиться можно только на запланированное мероприятие")

    existing = (
        await session.execute(
            select(EventAttendance).where(
                EventAttendance.event_id == event.id, EventAttendance.member_id == member.id
            )
        )
    ).scalar_one_or_none()

    if payload.going is None:
        if existing is not None:
            await session.delete(existing)
    elif existing is not None:
        existing.attended = payload.going
    else:
        session.add(EventAttendance(event_id=event.id, member_id=member.id, attended=payload.going))

    await session.commit()

    if payload.going is True:
        await notify_telegram(
            user.telegram_id,
            f"✅ Вы записались на «{event.title}» — {format_date_ru(event.date)}.",
        )

    return {"ok": True, "going": payload.going}
