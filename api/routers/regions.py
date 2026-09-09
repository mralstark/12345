"""Вкладка «Регионы» в Mini App — видна федеральному координатору и
координатору региона (план «Снизу вверх», §4, пересмотрено по просьбе:
вместо отдельной вкладки «Управление» — региональное управление в своей же
вкладке «Регионы», а назначение руководителя вузовской ячейки — в
«Вузовские ячейки», см. api/routers/cells.py). Заменяет собой бывшие кнопки
бота (handlers/create_account.py, handlers/manage_accounts.py — оба удалены).

Права:
  * создать регион, переименовать регион, удалить регион, назначить
    координатора — только federal (и superuser на переходный период, см.
    план §6);
  * назначить руководителя региона — federal любой регион, coordinator
    только в своих (accessible_region_ids уже даёт нужный набор: у
    federal/superuser это все активные регионы, у coordinator — свои).

Удаление региона — полное и необратимое (services/admin_actions.py::
delete_region_permanently), не архивирование: тот же принцип «одно действие
без мягкого варианта», что и у «Исключить» человека из состава (план §5)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user, get_db
from database.models import ROLE_COORDINATOR, ROLE_FEDERAL, ROLE_LEADER, ROLE_SUPERUSER, CoordinatorRegion, Region, User
from services.admin_actions import create_coordinator, create_leader, remove_coordinator, remove_leader, create_region, delete_region_permanently, update_region
from utils.access import accessible_region_ids
from utils.notify import send_role_assigned_notice, send_role_removed_notice

router = APIRouter(prefix="/regions", tags=["regions"])

_CAN_CREATE_REGION = (ROLE_FEDERAL, ROLE_SUPERUSER)
_CAN_ASSIGN_COORDINATOR = (ROLE_FEDERAL, ROLE_SUPERUSER)
_CAN_ASSIGN_LEADER = (ROLE_FEDERAL, ROLE_COORDINATOR, ROLE_SUPERUSER)
_CAN_SEE_TAB = (ROLE_FEDERAL, ROLE_COORDINATOR, ROLE_SUPERUSER)
# Переименовать/удалить регион — тот же верхний уровень, что и создать
# регион/назначить координатора: только федеральный.
_CAN_EDIT_REGION = _CAN_CREATE_REGION
_CAN_DELETE_REGION = _CAN_CREATE_REGION


@router.get("")
async def list_regions(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Полный список активных регионов с текущим руководителем/координатором
    — для вкладки «Регионы» (federal/coordinator/superuser)."""
    if user.role not in _CAN_SEE_TAB:
        raise HTTPException(403, "Недоступно")

    assignable = set(await accessible_region_ids(session, user))
    regions = list(
        (await session.execute(select(Region).where(Region.is_active.is_(True)).order_by(Region.name)))
        .scalars()
        .all()
    )

    # Все закрепления разом: куратор ведёт несколько отделений, и в строке
    # каждого надо показать остальные — снятие затрагивает их все, а раньше
    # об этом нигде не говорилось.
    coordinator_regions: dict[int, list[str]] = {}
    by_region: dict[int, User] = {}
    names = {region.id: region.name for region in regions}
    for coord, region_id in (
        await session.execute(
            select(User, CoordinatorRegion.region_id).join(
                CoordinatorRegion, CoordinatorRegion.coordinator_user_id == User.id
            )
        )
    ).all():
        by_region[region_id] = coord
        if region_id in names:
            coordinator_regions.setdefault(coord.id, []).append(names[region_id])

    items = []
    for region in regions:
        leader = await session.get(User, region.leader_user_id) if region.leader_user_id else None
        coord_row = by_region.get(region.id)
        items.append(
            {
                "id": region.id,
                "name": region.name,
                "leader_name": leader.full_name if leader else None,
                # Идентификаторы нужны, чтобы было кого снимать: по имени
                # человека не снимешь.
                "leader_user_id": leader.id if leader else None,
                "coordinator_name": coord_row.full_name if coord_row else None,
                "coordinator_user_id": coord_row.id if coord_row else None,
                "coordinator_other_regions": (
                    [n for n in coordinator_regions.get(coord_row.id, []) if n != region.name]
                    if coord_row is not None
                    else []
                ),
                "can_assign_leader": region.id in assignable,
            }
        )
    return {
        "items": items,
        "can_create_region": user.role in _CAN_CREATE_REGION,
        "can_assign_coordinator": user.role in _CAN_ASSIGN_COORDINATOR,
    }


class RegionIn(BaseModel):
    name: str = Field(min_length=2, max_length=128)


@router.post("")
async def create_region_endpoint(
    payload: RegionIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    if user.role not in _CAN_CREATE_REGION:
        raise HTTPException(403, "Создавать регионы вправе только федеральный координатор")
    try:
        region = await create_region(session, payload.name.strip())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"id": region.id, "name": region.name}


class RegionRename(BaseModel):
    name: str = Field(min_length=2, max_length=128)


@router.patch("/{region_id}")
async def rename_region_endpoint(
    region_id: int,
    payload: RegionRename,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    if user.role not in _CAN_EDIT_REGION:
        raise HTTPException(403, "Переименовывать регионы вправе только федеральный координатор")
    try:
        region = await update_region(session, region_id, name=payload.name.strip())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"id": region.id, "name": region.name}


@router.delete("/{region_id}")
async def delete_region_endpoint(
    region_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Полное и необратимое удаление — тот же принцип, что и «Исключить»
    человека (§5): одно действие, без промежуточного архивирования."""
    if user.role not in _CAN_DELETE_REGION:
        raise HTTPException(403, "Удалять регионы вправе только федеральный координатор")
    try:
        await delete_region_permanently(session, region_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True}


class AssignLeaderIn(BaseModel):
    member_id: int


@router.post("/{region_id}/leader")
async def assign_leader_endpoint(
    region_id: int,
    payload: AssignLeaderIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    # Явная проверка роли — не полагаемся только на accessible_region_ids:
    # у ROLE_LEADER она тоже вернёт его собственный регион (это для
    # видимости/редактирования состава, а не для назначения руководителя).
    if user.role not in _CAN_ASSIGN_LEADER:
        raise HTTPException(403, "Недоступно")
    if region_id not in await accessible_region_ids(session, user):
        raise HTTPException(403, "Регион недоступен для этой роли")
    try:
        target = await create_leader(session, region_id, member_id=payload.member_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    await send_role_assigned_notice(target, ROLE_LEADER)
    return {"id": target.id, "full_name": target.full_name}


@router.delete("/{region_id}/leader")
async def remove_leader_endpoint(
    region_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Снять руководителя, не назначая нового. Раньше сменить его было можно,
    а просто убрать — нет, и регион нельзя было оставить без руководителя."""
    if user.role not in _CAN_ASSIGN_LEADER:
        raise HTTPException(403, "Недоступно")
    if region_id not in await accessible_region_ids(session, user):
        raise HTTPException(403, "Регион недоступен для этой роли")
    try:
        removed = await remove_leader(session, region_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if removed is None:
        raise HTTPException(400, "У этого региона нет руководителя")
    await send_role_removed_notice(removed)
    return {"id": removed.id, "full_name": removed.full_name}


class AssignCoordinatorIn(BaseModel):
    member_id: int
    region_ids: list[int] = Field(min_length=1)


@router.post("/coordinators")
async def assign_coordinator_endpoint(
    payload: AssignCoordinatorIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    if user.role not in _CAN_ASSIGN_COORDINATOR:
        raise HTTPException(403, "Назначать координаторов вправе только федеральный координатор")
    try:
        target = await create_coordinator(session, payload.region_ids, member_id=payload.member_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    await send_role_assigned_notice(target, ROLE_COORDINATOR)
    return {"id": target.id, "full_name": target.full_name}


@router.delete("/coordinators/{user_id}")
async def remove_coordinator_endpoint(
    user_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Снять координатора со всех его регионов."""
    if user.role not in _CAN_ASSIGN_COORDINATOR:
        raise HTTPException(403, "Снимать координаторов вправе только федеральный координатор")
    try:
        removed = await remove_coordinator(session, user_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    await send_role_removed_notice(removed)
    return {"id": removed.id, "full_name": removed.full_name}
