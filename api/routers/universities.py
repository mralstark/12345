"""Региональный каталог вузов и его управление."""

import unicodedata

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user, get_db
from database.models import (
    ROLE_FEDERAL,
    ROLE_LEADER,
    ROLE_SUPERUSER,
    Member,
    Region,
    University,
    UniversityCell,
    User,
)
from utils.access import AccessDenied

router = APIRouter(prefix="/universities", tags=["universities"])


def _university_dict(university: University) -> dict:
    return {
        "id": university.id,
        "name": university.name,
        "region_id": university.region_id,
        "is_active": university.is_active,
    }


def _normalise_name(raw: str) -> str:
    name = unicodedata.normalize("NFKC", " ".join(raw.split()))
    if len(name) < 2:
        raise HTTPException(400, "Название вуза слишком короткое")
    return name


async def _require_catalog_manager(
    session: AsyncSession, user: User, region_id: int
) -> Region:
    region = await session.get(Region, region_id)
    if region is None or not region.is_active:
        raise HTTPException(400, "Регион не найден или архивирован")
    if user.role in (ROLE_SUPERUSER, ROLE_FEDERAL):
        return region
    if user.role == ROLE_LEADER and region.leader_user_id == user.id:
        return region
    raise AccessDenied("Каталог вузов доступен руководителю этого отделения и федеральному руководству")


async def _member_count(session: AsyncSession, university_id: int) -> int:
    return (
        await session.scalar(
            select(func.count(Member.id)).where(
                Member.university_id == university_id,
                Member.is_active.is_(True),
            )
        )
    ) or 0


@router.get("")
async def search_universities(
    q: str = Query(default="", max_length=255),
    region_id: int | None = None,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Активные вузы для автодополнения. Формы передают region_id и поэтому
    не могут случайно привязать человека к вузу другого отделения."""
    stmt = select(University).where(University.is_active.is_(True)).order_by(University.name)
    if region_id is not None:
        stmt = stmt.where(University.region_id == region_id)
    result = await session.execute(stmt.limit(5_000))
    items = list(result.scalars().all())

    # Регистронезависимый поиск считаем в Python: lower() в SQLite не
    # приводит кириллицу к нижнему регистру (тот же нюанс, что и в
    # api/routers/members.py::list_members).
    needle = q.strip().lower()
    if needle:
        items = [u for u in items if needle in u.name.lower()]

    return {"items": [_university_dict(u) for u in items[:20]]}


class UniversityIn(BaseModel):
    name: str = Field(min_length=2, max_length=128)
    region_id: int | None = None


class UniversityPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=128)
    is_active: bool | None = None


@router.get("/manage")
async def manage_universities(
    region_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Полный список региона, включая архив, для руководящего интерфейса."""
    await _require_catalog_manager(session, user, region_id)
    rows = list(
        (
            await session.execute(
                select(University)
                .where(University.region_id == region_id)
                .order_by(University.is_active.desc(), University.name)
            )
        ).scalars().all()
    )
    items = []
    for university in rows:
        item = _university_dict(university)
        item["members_total"] = await _member_count(session, university.id)
        items.append(item)
    return {"items": items}


@router.post("")
async def create_university(
    payload: UniversityIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Создаёт вуз в регионе без дублей по регистру и пробелам.

    Повторное добавление архивной записи восстанавливает её. Запись старого
    каталога без региона можно привязать к выбранному региону; запись другого
    региона перехватить нельзя.
    """
    if payload.region_id is None:
        if user.role not in (ROLE_FEDERAL, ROLE_SUPERUSER):
            raise HTTPException(403, "Создавать записи общего каталога может только федеральный координатор")
    else:
        await _require_catalog_manager(session, user, payload.region_id)

    name = _normalise_name(payload.name)

    key = name.lower()
    existing = (await session.execute(select(University))).scalars().all()
    for university in existing:
        if university.name.lower() == key:
            if payload.region_id is not None:
                if university.region_id not in (None, payload.region_id):
                    raise HTTPException(409, "ВУЗ уже относится к другому региону")
                university.region_id = payload.region_id
                university.is_active = True
                await session.commit()
            return _university_dict(university)

    university = University(name=name, region_id=payload.region_id)
    session.add(university)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = (
            await session.execute(select(University).where(University.name == name))
        ).scalar_one_or_none()
        if existing is not None:
            return _university_dict(existing)
        raise HTTPException(409, "ВУЗ с таким названием уже существует") from None
    await session.refresh(university)
    return _university_dict(university)


@router.patch("/{university_id}")
async def update_university(
    university_id: int,
    payload: UniversityPatch,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    university = await session.get(University, university_id)
    if university is None:
        raise HTTPException(404, "ВУЗ не найден")
    if university.region_id is None:
        if user.role not in (ROLE_FEDERAL, ROLE_SUPERUSER):
            raise AccessDenied("Записи без региона доступны только федеральному руководству")
    else:
        await _require_catalog_manager(session, user, university.region_id)

    data = payload.model_dump(exclude_unset=True)
    if "name" in data:
        name = _normalise_name(data["name"])
        rows = list((await session.execute(select(University))).scalars().all())
        if any(row.id != university.id and row.name.lower() == name.lower() for row in rows):
            raise HTTPException(409, "ВУЗ с таким названием уже существует")
        university.name = name
        # Название ячейки производно от вуза; поддерживаем их в согласованном
        # состоянии во всех регионах, где такая ячейка уже появилась.
        cells = list(
            (
                await session.execute(
                    select(UniversityCell).where(UniversityCell.university_id == university.id)
                )
            ).scalars().all()
        )
        for cell in cells:
            cell.name = name[:128]
    if "is_active" in data:
        university.is_active = data["is_active"]

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, "ВУЗ с таким названием уже существует") from None
    await session.refresh(university)
    result = _university_dict(university)
    result["members_total"] = await _member_count(session, university.id)
    return result
