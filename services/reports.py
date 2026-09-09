"""Сбор данных для отчёта по региону (ТЗ §13) — общий источник для XLSX и PDF."""

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    MEMBER_STATUS_LABELS,
    Category,
    Event,
    EventAttendance,
    Member,
    Region,
    Transaction,
    University,
    User,
)
from utils.balance_calc import get_balance, get_category_breakdown, get_totals


@dataclass
class RegionReport:
    region_name: str
    period_label: str
    start: date
    end: date
    balance: int
    income: int
    expense: int
    # ФИО, статус, телефон, др, дата вступления, вуз, факультет, курс, место работы
    members: list[tuple[str, str, str, str, str, str, str, str, str]] = field(default_factory=list)
    members_by_status: list[tuple[str, int]] = field(default_factory=list)
    transactions: list[tuple] = field(default_factory=list)  # дата, тип, категория, мероприятие, сумма, комментарий
    expense_by_category: list[tuple[str, int]] = field(default_factory=list)
    income_by_category: list[tuple[str, int]] = field(default_factory=list)
    events: list[tuple] = field(default_factory=list)  # дата, название, статус, план, факт, участников


async def gather_region_report(
    session: AsyncSession, region_id: int, start: date, end: date, period_label: str
) -> RegionReport:
    region = await session.get(Region, region_id)
    income, expense = await get_totals(session, region_id, start, end)

    report = RegionReport(
        region_name=region.name if region else str(region_id),
        period_label=period_label,
        start=start,
        end=end,
        balance=await get_balance(session, region_id),
        income=income,
        expense=expense,
    )

    members_result = await session.execute(
        select(Member, University.name)
        .outerjoin(University, University.id == Member.university_id)
        .where(Member.region_id == region_id, Member.is_active.is_(True))
        .order_by(Member.status, Member.full_name)
    )
    member_rows = list(members_result.all())
    report.members = [
        (
            member.full_name,
            MEMBER_STATUS_LABELS.get(member.status, member.status),
            member.phone or "",
            member.birth_date.strftime("%d.%m.%Y") if member.birth_date else "",
            member.activist_joined_at.strftime("%d.%m.%Y") if member.activist_joined_at else "",
            university_name or "",
            member.faculty or "",
            f"{member.course} курс" if member.course else "",
            member.workplace or "",
        )
        for member, university_name in member_rows
    ]
    counts: dict[str, int] = {}
    for member, _ in member_rows:
        counts[member.status] = counts.get(member.status, 0) + 1
    report.members_by_status = [
        (label, counts.get(key, 0)) for key, label in MEMBER_STATUS_LABELS.items()
    ]

    tx_result = await session.execute(
        select(Transaction, Category.name, Event.title, User.full_name)
        .join(Category, Category.id == Transaction.category_id, isouter=True)
        .join(Event, Event.id == Transaction.event_id, isouter=True)
        .join(User, User.id == Transaction.author_id, isouter=True)
        .where(Transaction.region_id == region_id, Transaction.date >= start, Transaction.date <= end)
        .order_by(Transaction.date)
    )
    report.transactions = [
        (
            tx.date,
            "Доход" if tx.type == "income" else "Расход",
            category or "Без категории",
            event_title or "",
            tx.amount,
            tx.comment or "",
            author or "",
        )
        for tx, category, event_title, author in tx_result.all()
    ]

    report.expense_by_category = [
        (name, amount) for name, _emoji, amount in await get_category_breakdown(session, region_id, "expense", start, end)
    ]
    report.income_by_category = [
        (name, amount) for name, _emoji, amount in await get_category_breakdown(session, region_id, "income", start, end)
    ]

    events_result = await session.execute(
        select(
            Event,
            func.count(EventAttendance.id).filter(EventAttendance.attended.is_(True)),
        )
        .join(EventAttendance, EventAttendance.event_id == Event.id, isouter=True)
        .where(Event.region_id == region_id, Event.date >= start, Event.date <= end)
        .group_by(Event.id)
        .order_by(Event.date)
    )
    events = events_result.all()

    fact_by_event: dict[int, int] = {}
    if events:
        fact_result = await session.execute(
            select(Transaction.event_id, func.sum(Transaction.amount))
            .where(
                Transaction.event_id.in_([event.id for event, _ in events]),
                Transaction.type == "expense",
            )
            .group_by(Transaction.event_id)
        )
        fact_by_event = {event_id: total or 0 for event_id, total in fact_result.all()}

    from database.models import EVENT_STATUS_LABELS

    report.events = [
        (
            event.date,
            event.title,
            EVENT_STATUS_LABELS.get(event.status, event.status),
            event.planned_budget,
            fact_by_event.get(event.id, 0),
            attended or 0,
        )
        for event, attended in events
    ]

    return report
