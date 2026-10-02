"""Общие правила Академии для личного и управленческого кабинетов."""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    MEMBER_STATUS_ACTIVIST, ROLE_COORDINATOR, ROLE_FEDERAL, ROLE_LEADER,
    ROLE_SUPERUSER, Member, MemberQuestProgress, QuestActivity, User,
)
from utils.access import AccessDenied, require_view
from utils.permissions import has_any_role


def can_manage_academy(user: User) -> bool:
    return has_any_role(user, (ROLE_SUPERUSER, ROLE_FEDERAL, ROLE_COORDINATOR, ROLE_LEADER))


async def require_academy(session: AsyncSession, user: User, member: Member) -> None:
    if not can_manage_academy(user):
        raise AccessDenied("Академией управляют руководитель региона и координаторы")
    await require_view(session, user, member.region_id)


def require_student(member: Member) -> None:
    if not member.is_active or member.status != MEMBER_STATUS_ACTIVIST:
        raise HTTPException(409, "Задания Академии доступны действующим корпорантам")


async def lock_progress(session: AsyncSession, member_id: int, quest_id: int) -> MemberQuestProgress:
    # Блокируем родительскую строку и при первом назначении, когда прогресса ещё нет.
    await session.execute(select(Member.id).where(Member.id == member_id).with_for_update())
    row = (await session.execute(select(MemberQuestProgress).where(
        MemberQuestProgress.member_id == member_id, MemberQuestProgress.quest_id == quest_id,
    ).with_for_update())).scalar_one_or_none()
    if row is None:
        row = MemberQuestProgress(member_id=member_id, quest_id=quest_id, count=0,
                                  stars_claimed=0, pending_count=0, earned_stars_floor=0)
        session.add(row)
    return row


def log_activity(session: AsyncSession, user: User, row: MemberQuestProgress, action: str,
                 note: str | None = None) -> None:
    session.add(QuestActivity(member_id=row.member_id, quest_id=row.quest_id,
                             actor_user_id=user.id, action=action, note=note))


def clear_assignment(row: MemberQuestProgress) -> None:
    row.assigned_by_user_id = None
    row.assigned_at = None
    row.assignment_note = None
