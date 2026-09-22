"""Управленческий обзор Академии по доступному региону."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user, get_db
from database.models import MEMBER_STATUS_LABELS, Member, MemberQuestProgress, Quest, User
from utils.access import actor_cell, require_view

router = APIRouter(prefix="/academy", tags=["academy"])


@router.get("")
async def academy_overview(
    region_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    await require_view(session, user, region_id)
    member_stmt = select(Member).where(Member.region_id == region_id, Member.is_active.is_(True))
    cell = await actor_cell(session, user)
    if cell is not None:
        member_stmt = member_stmt.where(Member.cell_id == cell.id)
    members = list((await session.execute(member_stmt.order_by(Member.full_name))).scalars().all())
    quests = list((await session.execute(
        select(Quest).where(Quest.is_active.is_(True)).order_by(Quest.position)
    )).scalars().all())
    member_ids = [member.id for member in members]
    rows = [] if not member_ids else list((await session.execute(
        select(MemberQuestProgress).where(MemberQuestProgress.member_id.in_(member_ids))
    )).scalars().all())
    by_member: dict[int, dict[int, MemberQuestProgress]] = {}
    for row in rows:
        by_member.setdefault(row.member_id, {})[row.quest_id] = row

    total_possible = sum(max(q.thresholds_list(), default=0) for q in quests)
    items = []
    for member in members:
        progress = by_member.get(member.id, {})
        earned = 0
        completed = 0
        pending = []
        assigned = 0
        for quest in quests:
            row = progress.get(quest.id)
            count = row.count if row else 0
            ceiling = max(quest.thresholds_list(), default=0)
            earned += min(count, ceiling)
            completed += int(bool(ceiling and count >= ceiling))
            if row and row.assigned_by_user_id is not None and count < ceiling:
                assigned += 1
            if row and row.pending_count:
                pending.append({
                    "quest_id": quest.id,
                    "title": quest.title,
                    "note": row.submitted_note,
                })
        items.append({
            "id": member.id,
            "full_name": member.full_name,
            "status_label": MEMBER_STATUS_LABELS.get(member.status, member.status),
            "completed": completed,
            "total": len(quests),
            "progress_percent": round(earned / total_possible * 100) if total_possible else 0,
            "pending_count": len(pending),
            "assigned_count": assigned,
            "pending": pending,
        })
    items.sort(key=lambda item: (-item["pending_count"], -item["assigned_count"], item["full_name"]))
    return {
        "items": items,
        "quest_count": len(quests),
        "pending_count": sum(item["pending_count"] for item in items),
        "assigned_count": sum(item["assigned_count"] for item in items),
    }
