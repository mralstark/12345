"""Повторяющиеся мероприятия (ТЗ §8): система сама создаёт записи по расписанию.

Запись, у которой стоит is_recurring, играет роль шаблона: она же — первое
проведение. Порождённые ею записи обычные (is_recurring=False) и ссылаются
на шаблон через recurrence_parent_id — их можно править и отменять поштучно,
не ломая расписание.
"""

import calendar
import logging
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import EVENT_STATUS_PLANNED, Event
from utils.tz import today as tz_today

logger = logging.getLogger(__name__)

# На сколько вперёд материализуем повторы. Больше — незачем: список мероприятий
# засорится записями на год вперёд, меньше — руководитель не увидит ближайшие.
HORIZON_DAYS = 90

RECURRENCE_RULES = ("weekly", "biweekly", "monthly")
RECURRENCE_LABELS = {"weekly": "Еженедельно", "biweekly": "Раз в две недели", "monthly": "Ежемесячно"}


def next_occurrence(current: date, rule: str) -> date:
    if rule == "weekly":
        return current + timedelta(days=7)
    if rule == "biweekly":
        return current + timedelta(days=14)
    if rule == "monthly":
        # Номер следующего месяца сквозной нумерацией: year*12 + (month-1) + 1.
        year, month_index = divmod(current.year * 12 + current.month, 12)
        month = month_index + 1
        # 31 января + месяц = 28/29 февраля: день подрезается по длине месяца.
        last_day = calendar.monthrange(year, month)[1]
        return date(year, month, min(current.day, last_day))
    raise ValueError(f"Неизвестное правило повтора: {rule}")


async def materialize_event(session: AsyncSession, template: Event, today: date | None = None) -> list[Event]:
    """Досоздаёт недостающие повторы шаблона до горизонта. Идемпотентна."""
    if not template.is_recurring or template.recurrence_rule not in RECURRENCE_RULES:
        return []

    today = today or tz_today()
    horizon = today + timedelta(days=HORIZON_DAYS)
    if template.recurrence_until and template.recurrence_until < horizon:
        horizon = template.recurrence_until

    result = await session.execute(
        select(func.max(Event.date)).where(Event.recurrence_parent_id == template.id)
    )
    last_date = result.scalar() or template.date

    created: list[Event] = []
    cursor = next_occurrence(last_date, template.recurrence_rule)
    while cursor <= horizon:
        event = Event(
            region_id=template.region_id,
            title=template.title,
            date=cursor,
            description=template.description,
            responsible_member_id=template.responsible_member_id,
            status=EVENT_STATUS_PLANNED,
            planned_budget=template.planned_budget,
            is_recurring=False,
            recurrence_parent_id=template.id,
        )
        session.add(event)
        created.append(event)
        cursor = next_occurrence(cursor, template.recurrence_rule)

    if created:
        await session.commit()
        logger.info("Повторяющееся мероприятие #%s: создано записей — %s", template.id, len(created))
    return created


async def materialize_all(session: AsyncSession, today: date | None = None) -> int:
    result = await session.execute(select(Event).where(Event.is_recurring.is_(True)))
    total = 0
    for template in result.scalars().all():
        total += len(await materialize_event(session, template, today=today))
    return total
