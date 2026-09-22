"""Модуль «Вузовские ячейки»: состав/мероприятия/бюджет одного вуза внутри
региона. Создание самой ячейки — по-прежнему вне Mini App, через
scripts/admin.py (cell-add) — так же, как и регион заводит только CLI.
Назначение руководителя ячейки — прямо здесь, в этой же вкладке (см.
POST /{cell_id}/leader ниже) — руководитель региона видит своих ячейки и
сразу назначает; раньше это делалось через отдельную кнопку в боте
(handlers/create_account.py, удалён при переходе на самостоятельную
регистрацию, план «Снизу вверх», §4)."""

from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user, get_db
from database.models import (
    MEMBER_STATUS_LABELS,
    ROLE_CELL_LEADER,
    ROLE_LEADER,
    ROLE_SUPERUSER,
    Event,
    Member,
    Region,
    University,
    UniversityCell,
    User,
)
from services.admin_actions import create_cell_leader, remove_cell_leader
from utils.access import AccessDenied, actor_cell, require_edit, require_view
from utils.balance_calc import get_cell_totals
from utils.notify import send_role_assigned_notice, send_role_removed_notice
from utils.tz import today as tz_today

router = APIRouter(prefix="/cells", tags=["cells"])


class CellPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=128)
    genitive_name: str | None = Field(default=None, max_length=128)
    vk_url: str | None = Field(default=None, max_length=255)
    allocated_budget: int | None = Field(default=None, ge=0, le=2_147_483_647)

    @field_validator("vk_url")
    @classmethod
    def validate_vk_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return value
        value = value.strip()
        parsed = urlparse(value)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or not (hostname == "vk.com" or hostname.endswith(".vk.com"))
        ):
            raise ValueError("Ссылка должна вести на HTTPS-страницу vk.com")
        return value


def _require_region_leader(user: User) -> None:
    """Метаданные самой ячейки (название, VK, бюджет) правит руководитель
    региона — не руководитель ячейки: require_edit(region_id) пропускает и
    того, и другого (иначе ячейка не смогла бы работать со своим составом),
    здесь нужно сузить дополнительно."""
    if user.role == ROLE_CELL_LEADER:
        raise AccessDenied("Данные ячейки правит руководитель региона")


async def _cell_brief(session: AsyncSession, cell: UniversityCell) -> dict:
    leader = await session.get(User, cell.leader_user_id) if cell.leader_user_id else None
    members_total = (
        await session.execute(
            select(func.count(Member.id)).where(Member.cell_id == cell.id, Member.is_active.is_(True))
        )
    ).scalar() or 0
    _, expense = await get_cell_totals(session, cell.id)
    next_event = (
        await session.execute(
            select(Event.title, Event.date)
            .where(Event.cell_id == cell.id, Event.date >= tz_today())
            .order_by(Event.date)
            .limit(1)
        )
    ).first()

    return {
        "id": cell.id,
        "region_id": cell.region_id,
        "name": cell.name,
        "genitive_name": cell.genitive_name,
        "leader_name": leader.full_name if leader else None,
        "vk_url": cell.vk_url,
        "allocated_budget": cell.allocated_budget,
        "fact_expense": expense,
        "members_total": members_total,
        "next_event": {"title": next_event[0], "date": next_event[1].isoformat()} if next_event else None,
        "is_active": cell.is_active,
    }


@router.get("")
async def list_cells(
    region_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    await require_view(session, user, region_id)

    stmt = select(UniversityCell).where(UniversityCell.region_id == region_id, UniversityCell.is_active.is_(True))
    own_cell = await actor_cell(session, user)
    if own_cell is not None:
        # Руководитель ячейки не листает соседние ячейки — только свою.
        stmt = stmt.where(UniversityCell.id == own_cell.id)

    result = await session.execute(stmt.order_by(UniversityCell.name))
    cells = list(result.scalars().all())
    items = [await _cell_brief(session, cell) for cell in cells]
    # Пустые ячейки не показываем. Ячейка заводится сама от вуза, вписанного
    # человеку (utils/university_cells::resolve_member_cell), и остаётся, если
    # вуз потом исправили: в Барнауле так осталась «АСК СПБ» — заведена
    # опечаткой и с тех пор пустая. Прятать безопасно: назовёт кто-нибудь этот
    # вуз снова — resolve_member_cell найдёт ту же ячейку, и она вернётся со
    # всей своей историей.
    return {"items": [i for i in items if i["members_total"] > 0]}


@router.get("/{cell_id}")
async def get_cell(
    cell_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    cell = await session.get(UniversityCell, cell_id)
    if cell is None:
        raise HTTPException(404, "Ячейка не найдена")
    await require_view(session, user, cell.region_id)
    own_cell = await actor_cell(session, user)
    if own_cell is not None and own_cell.id != cell.id:
        raise AccessDenied("Доступно только для своей вузовской ячейки")

    data = await _cell_brief(session, cell)

    members_result = await session.execute(
        select(Member)
        .where(Member.cell_id == cell.id, Member.is_active.is_(True))
        .order_by(Member.status, Member.full_name)
    )
    events_result = await session.execute(
        select(Event).where(Event.cell_id == cell.id).order_by(Event.date.desc()).limit(50)
    )

    members = list(members_result.scalars().all())
    # Кто из состава вообще может стать руководителем: назначение роли — это
    # повышение существующего аккаунта (services/admin_actions::_resolve_or_promote),
    # а человек без саморегистрации его ещё не имеет. Без этого признака список
    # давал нажать на любого и упереться в отказ уже после нажатия.
    with_account = set(
        (
            await session.execute(
                select(User.member_id).where(User.member_id.in_([m.id for m in members] or [0]))
            )
        ).scalars().all()
    )
    data["members"] = [
        {
            "id": m.id,
            "full_name": m.full_name,
            "status": m.status,
            "has_account": m.id in with_account,
            # Подпись статуса — чтобы список состава ячейки читался так же,
            # как «Состав» отделения, а не голыми фамилиями.
            "status_label": MEMBER_STATUS_LABELS.get(m.status, m.status),
        }
        for m in members
    ]
    # Вуз, от которого ячейка произошла: она производная от него
    # (utils/university_cells.resolve_member_cell), и на экране этого не
    # хватало, чтобы понять, откуда она взялась.
    university = await session.get(University, cell.university_id) if cell.university_id else None
    data["university_name"] = university.name if university is not None else None
    data["events"] = [
        {"id": e.id, "title": e.title, "date": e.date.isoformat(), "status": e.status}
        for e in events_result.scalars().all()
    ]
    return data


@router.patch("/{cell_id}")
async def update_cell(
    cell_id: int,
    payload: CellPatch,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    cell = await session.get(UniversityCell, cell_id)
    if cell is None:
        raise HTTPException(404, "Ячейка не найдена")
    await require_edit(session, user, cell.region_id)
    _require_region_leader(user)

    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        if isinstance(value, str):
            value = value.strip() or None
        setattr(cell, field, value)

    await session.commit()
    await session.refresh(cell)
    return await _cell_brief(session, cell)


async def _cell_leader_target(session: AsyncSession, user: User, cell_id: int) -> UniversityCell:
    """Кто вправе распоряжаться должностью в этой ячейке — руководитель
    отделения (и superuser на переходный период), не координатор и не
    федеральный: та же граница, что раньше проверялась в
    handlers/create_account.py."""
    cell = await session.get(UniversityCell, cell_id)
    if cell is None:
        raise HTTPException(404, "Ячейка не найдена")
    from utils.permissions import has_role

    if user.role != ROLE_LEADER and not has_role(user, ROLE_SUPERUSER):
        raise HTTPException(403, "Недоступно")
    if not has_role(user, ROLE_SUPERUSER):
        region = (await session.execute(select(Region).where(Region.leader_user_id == user.id))).scalar_one_or_none()
        if region is None or region.id != cell.region_id:
            raise HTTPException(403, "Доступно только руководителю этого региона")
    return cell


class AssignCellLeaderIn(BaseModel):
    member_id: int


@router.post("/{cell_id}/leader")
async def assign_cell_leader(
    cell_id: int,
    payload: AssignCellLeaderIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Назначает руководителя ячейки — доступно руководителю региона (и
    superuser на переходный период), не координатору/федеральному: та же
    граница, что раньше проверялась в handlers/create_account.py."""
    await _cell_leader_target(session, user, cell_id)

    try:
        target = await create_cell_leader(session, cell_id, member_id=payload.member_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    await send_role_assigned_notice(target, ROLE_CELL_LEADER)
    return {"id": target.id, "full_name": target.full_name}


@router.delete("/{cell_id}/leader")
async def drop_cell_leader(
    cell_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Снять руководителя ячейки. Раньше его можно было только сменить —
    а если человек выпустился и заменить его некем, ячейка оставалась с
    бывшим во главе навсегда."""
    cell = await _cell_leader_target(session, user, cell_id)
    person = await remove_cell_leader(session, cell.id)
    if person is not None:
        await send_role_removed_notice(person)
    return {"ok": True, "removed": person.full_name if person else None}
