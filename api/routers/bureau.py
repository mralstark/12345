"""Федеральное бюро: кто в нём состоит и с какой должностью.

Бюро — третий уровень рядом с отделением и ячейкой. Членство в нём лежит
поверх состава: человек как числился в своём отделении, так и числится
(database/models.py::BureauMember).

Кто что может:

* смотреть — все, кроме активистов, плюс руководство независимо от статуса.
  Руководителя-активиста иначе не пустило бы туда, куда пускают его
  подопечных, и это выглядело бы поломкой;
* править — федеральный координатор и админ.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user, get_db
from database.models import (
    MEMBER_STATUS_ACTIVIST,
    ROLE_FEDERAL,
    ROLE_SUPERUSER,
    BureauMember,
    BureauRegion,
    CoordinatorRegion,
    Member,
    Region,
    User,
)
from services.news import avatar_url_for_user, short_name
from utils.access import AccessDenied
from utils.permissions import has_any_role

router = APIRouter(prefix="/bureau", tags=["bureau"])

EDITOR_ROLES = (ROLE_SUPERUSER, ROLE_FEDERAL)


async def _can_view(session: AsyncSession, user: User) -> bool:
    """Корпоранту список не показываем — так решило руководство. Но роль важнее
    статуса: руководитель видит бюро в любом случае."""
    if user.role != "participant" or has_any_role(user, EDITOR_ROLES):
        return True
    if user.member_id is None:
        return False
    member = await session.get(Member, user.member_id)
    return member is not None and member.status != MEMBER_STATUS_ACTIVIST


def _can_edit(user: User) -> bool:
    return has_any_role(user, EDITOR_ROLES)


class BureauIn(BaseModel):
    # Приходит карточка из состава, а не аккаунт: человека выбирают в том же
    # окне, что и при назначении на должность (webapp/app.js::personPicker).
    member_id: int
    title: str = Field(min_length=2, max_length=120)
    region_ids: list[int] = Field(default_factory=list, max_length=100)


class BureauPatch(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=120)
    sort_order: int | None = Field(default=None, ge=-2_147_483_648, le=2_147_483_647)
    region_ids: list[int] | None = Field(default=None, max_length=100)


async def _row_dict(session: AsyncSession, row: BureauMember) -> dict:
    person = await session.get(User, row.user_id)
    member = await session.get(Member, person.member_id) if person and person.member_id else None
    region = await session.get(Region, member.region_id) if member else None
    regions = (
        await session.execute(
            select(Region)
            .join(BureauRegion, BureauRegion.region_id == Region.id)
            .where(BureauRegion.bureau_member_id == row.id)
            .order_by(Region.name)
        )
    ).scalars().all()
    return {
        "id": row.id,
        "user_id": row.user_id,
        "name": short_name(member.full_name if member else (person.full_name if person else "")),
        "title": row.title,
        "region": region.name if region else None,
        "avatar": await avatar_url_for_user(session, person),
        "sort_order": row.sort_order,
        "region_ids": [r.id for r in regions],
        "regions": [r.name for r in regions],
    }


async def _sync_regions(session: AsyncSession, row: BureauMember, region_ids: list[int]) -> None:
    unique_ids = list(dict.fromkeys(region_ids))
    if unique_ids:
        count = len((await session.execute(
            select(Region.id).where(Region.id.in_(unique_ids), Region.is_active.is_(True))
        )).scalars().all())
        if count != len(unique_ids):
            raise HTTPException(400, "Один из регионов не найден или архивирован")
    await session.execute(delete(BureauRegion).where(BureauRegion.bureau_member_id == row.id))
    for region_id in unique_ids:
        session.add(BureauRegion(bureau_member_id=row.id, region_id=region_id))


@router.get("")
async def list_bureau(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    if not await _can_view(session, user):
        raise AccessDenied("Состав бюро виден членам Братства")
    rows = (
        await session.execute(select(BureauMember).order_by(BureauMember.sort_order, BureauMember.id))
    ).scalars().all()
    return {
        "items": [await _row_dict(session, row) for row in rows],
        "can_edit": _can_edit(user),
        "regions": [
            {"id": r.id, "name": r.name}
            for r in (await session.execute(select(Region).where(Region.is_active.is_(True)).order_by(Region.name))).scalars().all()
        ],
    }


@router.post("")
async def add_to_bureau(
    payload: BureauIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    if not _can_edit(user):
        raise AccessDenied("Состав бюро правит федеральный координатор")

    person = (
        await session.execute(select(User).where(User.member_id == payload.member_id))
    ).scalar_one_or_none()
    if person is None:
        raise HTTPException(400, "У этого человека нет личного кабинета — в бюро его не добавить")

    already = (
        await session.execute(select(BureauMember).where(BureauMember.user_id == person.id))
    ).scalar_one_or_none()
    if already is not None:
        raise HTTPException(409, "Этот человек уже в бюро")

    last = (
        await session.execute(select(BureauMember.sort_order).order_by(BureauMember.sort_order.desc()))
    ).scalars().first()
    row = BureauMember(user_id=person.id, title=payload.title.strip(), sort_order=(last or 0) + 1)
    person.is_coordinator = True
    session.add(row)
    await session.flush()
    await _sync_regions(session, row, payload.region_ids)
    await session.commit()
    await session.refresh(row)
    return await _row_dict(session, row)


@router.patch("/{row_id}")
async def update_in_bureau(
    row_id: int,
    payload: BureauPatch,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    if not _can_edit(user):
        raise AccessDenied("Состав бюро правит федеральный координатор")
    row = await session.get(BureauMember, row_id)
    if row is None:
        raise HTTPException(404, "Запись не найдена")

    data = payload.model_dump(exclude_unset=True)
    if data.get("title") is not None:
        row.title = data["title"].strip()
    if data.get("sort_order") is not None:
        row.sort_order = data["sort_order"]
    if data.get("region_ids") is not None:
        await _sync_regions(session, row, data["region_ids"])
    await session.commit()
    await session.refresh(row)
    return await _row_dict(session, row)


@router.delete("/{row_id}")
async def remove_from_bureau(
    row_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    if not _can_edit(user):
        raise AccessDenied("Состав бюро правит федеральный координатор")
    row = await session.get(BureauMember, row_id)
    if row is None:
        raise HTTPException(404, "Запись не найдена")
    person = await session.get(User, row.user_id)
    await session.execute(delete(BureauRegion).where(BureauRegion.bureau_member_id == row.id))
    await session.delete(row)
    await session.flush()
    if person is not None:
        has_assignment = (await session.execute(
            select(CoordinatorRegion.id).where(CoordinatorRegion.coordinator_user_id == person.id)
        )).scalars().first()
        if has_assignment is None:
            person.is_coordinator = False
    await session.commit()
    return {"ok": True}
