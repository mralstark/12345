"""Сохранение прочтения разделов; курсор подтверждает только уже полученные записи."""
from sqlalchemy import func, select

from database.models import Event, Member, NewsPost, SectionRead, User
from utils.access import AccessDenied, actor_cell, require_view
from utils.tz import today


async def scope_query(session, user: User, section: str, region_id: int | None = None):
    if section == "news":
        return select(NewsPost.id), 0
    if section != "events":
        raise AccessDenied("Неизвестный раздел")
    cell_id = None
    if region_id is None:
        member = await session.get(Member, user.member_id) if user.member_id else None
        if member is None or not member.is_active:
            raise AccessDenied("Личный кабинет недоступен")
        region_id = member.region_id
        scope_id = -region_id  # Личный просмотр не гасит события кабинета управления.
    else:
        await require_view(session, user, region_id)
        cell = await actor_cell(session, user)
        cell_id = cell.id if cell else None
        scope_id = region_id
    query = select(Event.id).where(Event.region_id == region_id,
                                   Event.date >= today(), Event.status == "planned")
    if cell_id is not None:
        query = query.where(Event.cell_id == cell_id)
    return query, scope_id


async def unseen_count(session, user, section, region_id=None) -> int:
    query, scope = await scope_query(session, user, section, region_id)
    seen = (await session.execute(select(SectionRead.last_item_id).where(
        SectionRead.user_id == user.id, SectionRead.section == section, SectionRead.scope_id == scope,
    ))).scalar_one_or_none() or 0
    if section == "news":
        query = query.where(NewsPost.author_user_id != user.id)
    return (await session.execute(select(func.count()).select_from(query.where(
        (Event.id if section == "events" else NewsPost.id) > seen,
    ).subquery()))).scalar_one()


async def mark_seen(session, user, section, cursor, region_id=None):
    query, scope = await scope_query(session, user, section, region_id)
    maximum = (await session.execute(select(func.max(query.subquery().c.id)))).scalar() or 0
    if cursor < 0 or cursor > maximum:
        raise AccessDenied("Курсор раздела недействителен")
    await session.execute(select(User.id).where(User.id == user.id).with_for_update())
    row = (await session.execute(select(SectionRead).where(
        SectionRead.user_id == user.id, SectionRead.section == section, SectionRead.scope_id == scope,
    ).with_for_update())).scalar_one_or_none()
    if row is None:
        row = SectionRead(user_id=user.id, section=section, scope_id=scope, last_item_id=cursor)
        session.add(row)
    else:
        row.last_item_id = max(row.last_item_id, cursor)
    await session.commit()
