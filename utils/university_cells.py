"""Автопривязка человека к вузовской ячейке по вузу, выбранному в «Составе».

Раньше ячейку руководитель выбирал вручную; теперь она целиком производная
от вуза человека — выбрали/вписали вуз, тут же нашлась или завелась ячейка с
этим вузом в этом регионе (api/routers/members.py вызывает это на
создании/правке человека). Смену вуза на другой просто переносит человека в
другую ячейку — старая при этом не удаляется и не деактивируется, могут
вернуться."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import University, UniversityCell


async def resolve_member_cell(
    session: AsyncSession, region_id: int, university_id: int | None
) -> UniversityCell | None:
    """None на входе — человек «региональный», без привязки к вузу/ячейке."""
    if university_id is None:
        return None

    cell = (
        await session.execute(
            select(UniversityCell).where(
                UniversityCell.region_id == region_id,
                UniversityCell.university_id == university_id,
            )
        )
    ).scalar_one_or_none()
    if cell is not None:
        return cell

    university = await session.get(University, university_id)
    if university is None:
        return None

    # Ячейка могла быть заведена вручную ещё до появления каталога — с тем же
    # названием, но без university_id. Не плодим дубль с тем же именем в том
    # же регионе, а связываем найденную с каталогом задним числом.
    unlinked = (
        await session.execute(
            select(UniversityCell).where(
                UniversityCell.region_id == region_id, UniversityCell.university_id.is_(None)
            )
        )
    ).scalars().all()
    key = university.name.strip().lower()
    for candidate in unlinked:
        if candidate.name.strip().lower() == key:
            candidate.university_id = university_id
            await session.flush()
            return candidate

    cell = UniversityCell(region_id=region_id, university_id=university_id, name=university.name)
    session.add(cell)
    await session.flush()
    return cell
