"""Сводная аналитика по доступным регионам (ТЗ §12)."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user, get_db
from database.models import (
    EVENT_STATUS_DONE,
    MEMBER_STATUS_LABELS,
    SUPERVISOR_ROLES,
    Event,
    Member,
    Region,
    User,
)
from utils.access import AccessDenied, accessible_region_ids
from utils.balance_calc import get_balance, get_totals
from utils.period import resolve_period
from utils.permissions import has_any_role

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/regions")
async def regions_dashboard(
    period: str = Query(default="month", max_length=16),
    offset: int = Query(default=0, ge=-1200, le=1200),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Единый дашборд сравнения регионов: состав, баланс, активность мероприятий."""
    if not has_any_role(user, SUPERVISOR_ROLES):
        raise AccessDenied("Сводная аналитика доступна координаторам и федеральному")

    region_ids = await accessible_region_ids(session, user)
    start, end, label = resolve_period(period, offset)

    if not region_ids:
        return {"period_label": label, "items": [], "totals": {}}

    regions_result = await session.execute(select(Region).where(Region.id.in_(region_ids)).order_by(Region.name))
    regions = list(regions_result.scalars().all())

    members_result = await session.execute(
        select(Member.region_id, Member.status, func.count(Member.id))
        .where(Member.region_id.in_(region_ids), Member.is_active.is_(True))
        .group_by(Member.region_id, Member.status)
    )
    members_map: dict[int, dict[str, int]] = {}
    for region_id, status, count in members_result.all():
        members_map.setdefault(region_id, {})[status] = count

    new_members_result = await session.execute(
        select(Member.region_id, func.count(Member.id))
        .where(
            Member.region_id.in_(region_ids),
            Member.is_active.is_(True),
            Member.activist_joined_at.is_not(None),
            Member.activist_joined_at >= start,
            Member.activist_joined_at <= end,
        )
        .group_by(Member.region_id)
    )
    new_members_map = dict(new_members_result.all())

    events_result = await session.execute(
        select(Event.region_id, func.count(Event.id))
        .where(Event.region_id.in_(region_ids), Event.date >= start, Event.date <= end)
        .group_by(Event.region_id)
    )
    events_map = dict(events_result.all())

    done_result = await session.execute(
        select(Event.region_id, func.count(Event.id))
        .where(
            Event.region_id.in_(region_ids),
            Event.date >= start,
            Event.date <= end,
            Event.status == EVENT_STATUS_DONE,
        )
        .group_by(Event.region_id)
    )
    done_map = dict(done_result.all())

    leaders_result = await session.execute(
        select(Region.id, User.full_name).join(User, User.id == Region.leader_user_id).where(
            Region.id.in_(region_ids)
        )
    )
    leaders_map = dict(leaders_result.all())

    items = []
    totals = {"members": 0, "new_members": 0, "balance": 0, "income": 0, "expense": 0, "events": 0}
    for region in regions:
        income, expense = await get_totals(session, region.id, start, end)
        balance = await get_balance(session, region.id)
        by_status = members_map.get(region.id, {})
        members_total = sum(by_status.values())

        items.append(
            {
                "region_id": region.id,
                "region": region.name,
                "leader": leaders_map.get(region.id),
                "members_total": members_total,
                "members_by_status": [
                    {"status": key, "label": value, "count": by_status.get(key, 0)}
                    for key, value in MEMBER_STATUS_LABELS.items()
                ],
                "new_members": new_members_map.get(region.id, 0),
                "balance": balance,
                "income": income,
                "expense": expense,
                "events_total": events_map.get(region.id, 0),
                "events_done": done_map.get(region.id, 0),
            }
        )

        totals["members"] += members_total
        totals["new_members"] += new_members_map.get(region.id, 0)
        totals["balance"] += balance
        totals["income"] += income
        totals["expense"] += expense
        totals["events"] += events_map.get(region.id, 0)

    return {"period_label": label, "period": {"kind": period, "offset": offset}, "items": items, "totals": totals}
