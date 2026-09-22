"""Правила видимости из ТЗ §3, в одном месте.

Смысл ролей в двух словах:
  * superuser  — видит и правит всё (техническая роль);
  * federal    — видит все регионы, правит данные региона только если ему это явно
                 позволено (см. can_edit_region) — по ТЗ координаторы работают
                 с регионами «в режиме просмотра»;
  * coordinator— то же самое, но по своему подмножеству регионов;
  * leader     — один свой регион, с правом изменения;
  * cell_leader— тот же регион, что и у leader, но видит и правит только свою
                 вузовскую ячейку внутри него — см. actor_cell() и то, как
                 её используют api/routers/members.py, events.py, finance.py
                 (дополнительный фильтр по cell_id поверх region_id).
"""

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    MEMBER_STATUS_MEMBER,
    ROLE_CELL_LEADER,
    ROLE_COORDINATOR,
    ROLE_FEDERAL,
    ROLE_LEADER,
    ROLE_SUPERUSER,
    BureauMember,
    BureauRegion,
    CoordinatorRegion,
    Member,
    Region,
    UniversityCell,
    User,
)
from utils.permissions import has_any_role, has_role


class AccessDenied(Exception):
    """Пользователь не имеет права на этот регион/объект."""


async def actor_cell(session: AsyncSession, user: User) -> UniversityCell | None:
    """Если пользователь — руководитель вузовской ячейки, сама ячейка; иначе None.

    Используется в списковых и по-объектных эндпоинтах, чтобы дополнительно
    сузить region_id-доступ до одной ячейки: сам по себе can_edit_region для
    ROLE_CELL_LEADER даёт доступ на уровень региона (иначе require_edit(region_id)
    не пройдёт), а actor_cell() — это второй, более узкий слой поверх него.
    """
    if user.role != ROLE_CELL_LEADER or has_any_role(user, (ROLE_SUPERUSER, ROLE_FEDERAL, ROLE_COORDINATOR)):
        return None
    return (
        await session.execute(select(UniversityCell).where(UniversityCell.leader_user_id == user.id))
    ).scalar_one_or_none()


async def accessible_region_ids(session: AsyncSession, user: User) -> list[int]:
    """Регионы, которые пользователь вправе видеть, по возрастанию id."""
    if has_any_role(user, (ROLE_SUPERUSER, ROLE_FEDERAL)):
        result = await session.execute(select(Region.id).where(Region.is_active.is_(True)).order_by(Region.id))
        return list(result.scalars().all())

    region_ids: set[int] = set()
    if has_role(user, ROLE_COORDINATOR):
        result = await session.execute(
            select(CoordinatorRegion.region_id)
            .join(Region, Region.id == CoordinatorRegion.region_id)
            .where(CoordinatorRegion.coordinator_user_id == user.id, Region.is_active.is_(True))
            .order_by(CoordinatorRegion.region_id)
        )
        region_ids.update(result.scalars().all())

    bureau_result = await session.execute(
        select(BureauRegion.region_id)
        .join(BureauMember, BureauMember.id == BureauRegion.bureau_member_id)
        .join(Region, Region.id == BureauRegion.region_id)
        .where(BureauMember.user_id == user.id, Region.is_active.is_(True))
    )
    region_ids.update(bureau_result.scalars().all())

    if user.role == ROLE_LEADER:
        result = await session.execute(
            select(Region.id).where(Region.leader_user_id == user.id, Region.is_active.is_(True))
        )
        region_ids.update(result.scalars().all())

    if user.role == ROLE_CELL_LEADER:
        # Архив региона (или самой ячейки) обязан закрыть доступ и его
        # руководителю — иначе «архивировать» ничего на самом деле не прячет.
        cell = await actor_cell(session, user)
        if cell is None or not cell.is_active:
            return []
        region = await session.get(Region, cell.region_id)
        if region is not None and region.is_active:
            region_ids.add(cell.region_id)

    return sorted(region_ids)


async def accessible_regions(session: AsyncSession, user: User) -> list[Region]:
    region_ids = await accessible_region_ids(session, user)
    if not region_ids:
        return []
    result = await session.execute(select(Region).where(Region.id.in_(region_ids)).order_by(Region.name))
    return list(result.scalars().all())


async def can_view_region(session: AsyncSession, user: User, region_id: int) -> bool:
    return region_id in await accessible_region_ids(session, user)


async def can_edit_region(session: AsyncSession, user: User, region_id: int) -> bool:
    """Изменять данные региона (состав, финансы, мероприятия, документы) может
    руководитель этого региона — и superuser, которому по ТЗ доступно всё для отладки.
    Координаторы и федеральный — только просмотр (ТЗ §6, §7, §9).

    Руководитель ячейки тоже проходит эту проверку для родительского региона —
    иначе require_edit(region_id) отказал бы ему целиком. Сужение до его
    собственной ячейки (не всего региона) — отдельный, более узкий слой
    actor_cell() поверх этой проверки в самих роутерах (members/events/finance)."""
    if has_role(user, ROLE_SUPERUSER):
        return True
    if user.role == ROLE_LEADER:
        region = await session.get(Region, region_id)
        return region is not None and region.leader_user_id == user.id
    if user.role == ROLE_CELL_LEADER:
        cell = await actor_cell(session, user)
        return cell is not None and cell.region_id == region_id
    return False


async def require_view(session: AsyncSession, user: User, region_id: int) -> None:
    if not await can_view_region(session, user, region_id):
        raise AccessDenied("Регион недоступен для этой роли")


async def require_edit(session: AsyncSession, user: User, region_id: int) -> None:
    if not await can_edit_region(session, user, region_id):
        raise AccessDenied("Изменение данных региона доступно только его руководителю")


async def default_region_id(session: AsyncSession, user: User) -> int | None:
    """Регион, который открывается по умолчанию: у руководителя — единственный свой,
    у координатора и федерального — первый доступный."""
    region_ids = await accessible_region_ids(session, user)
    return region_ids[0] if region_ids else None


async def subordinates(session: AsyncSession, user: User) -> list[User]:
    """Кому руководитель может поставить задачу внутри своего отделения — всем,
    кто там есть, без оглядки на статус.

    Раньше здесь была горизонталь: посвящённые в Братство ставили задачи друг
    другу. От неё отказались — у поручения между равными нет ответственного, а
    постановка задачи это управленческое действие, и место ему в кабинете
    управления. Проверено перед отказом: горизонталью не воспользовался никто.

    Руководитель ячейки ограничен своей ячейкой: он управляет её частью
    отделения, а не всем отделением.
    """
    region_ids = await accessible_region_ids(session, user)
    if not region_ids:
        return []

    stmt = (
        select(User)
        .join(Member, Member.id == User.member_id)
        .where(
            Member.region_id.in_(region_ids),
            Member.is_active.is_(True),
            User.is_active.is_(True),
            User.id != user.id,
        )
    )
    cell = await actor_cell(session, user)
    if cell is not None:
        stmt = stmt.where(Member.cell_id == cell.id)

    return list((await session.execute(stmt)).scalars().all())


async def correspondents(session: AsyncSession, user: User) -> list[User]:
    """Кому пользователь может писать в «Почте» и ставить задачи (ТЗ §10).

    Два круга, и они складываются. Вертикаль по должности — вверх и вниз, без
    связей между регионами (_correspondents_by_role): так руководитель пишет
    координатору, а федеральный — руководителям. И вниз, в свой состав
    (subordinates): руководитель отделения ставит задачу любому своему
    человеку, руководитель ячейки — любому в своей ячейке.

    У рядового человека нет ни того, ни другого: задачу он получает, а не
    раздаёт. Так и задумано — постановка задачи управленческое действие.
    """
    people = {u.id: u for u in await _correspondents_by_role(session, user)}
    for person in await subordinates(session, user):
        people[person.id] = person
    people.pop(user.id, None)
    return sorted(people.values(), key=lambda u: u.full_name)


async def _correspondents_by_role(session: AsyncSession, user: User) -> list[User]:
    """Вертикаль по должности: вверх и вниз, без горизонтальных связей
    между регионами."""
    if has_role(user, ROLE_SUPERUSER):
        result = await session.execute(
            select(User).where(User.id != user.id, User.is_active.is_(True)).order_by(User.full_name)
        )
        return list(result.scalars().all())

    if has_any_role(user, (ROLE_FEDERAL, ROLE_COORDINATOR)):
        region_ids = await accessible_region_ids(session, user)
        # Вниз: руководители доступных регионов.
        leaders_stmt = (
            select(User)
            .join(Region, Region.leader_user_id == User.id)
            .where(Region.id.in_(region_ids), User.is_active.is_(True))
        )
        result = await session.execute(leaders_stmt)
        people = {u.id: u for u in result.scalars().all()}

        # Вверх и вбок: федеральный видит всех координаторов, координатор — федерального.
        if has_role(user, ROLE_FEDERAL):
            result = await session.execute(
                select(User).where(or_(User.role == ROLE_COORDINATOR, User.is_coordinator.is_(True)), User.is_active.is_(True))
            )
        else:
            result = await session.execute(
                select(User).where(or_(User.role == ROLE_FEDERAL, User.is_federal.is_(True)), User.is_active.is_(True))
            )
        for u in result.scalars().all():
            people[u.id] = u

        people.pop(user.id, None)
        return sorted(people.values(), key=lambda u: u.full_name)

    if user.role == ROLE_LEADER:
        # Руководитель пишет вверх (координатору, федеральному) и вниз
        # (руководителям своих вузовских ячеек — симметрично тому, как
        # координатор пишет вниз руководителям регионов).
        region_ids = await accessible_region_ids(session, user)
        people: dict[int, User] = {}
        if region_ids:
            result = await session.execute(
                select(User)
                .join(CoordinatorRegion, CoordinatorRegion.coordinator_user_id == User.id)
                .where(CoordinatorRegion.region_id.in_(region_ids), User.is_active.is_(True))
            )
            for u in result.scalars().all():
                people[u.id] = u
            result = await session.execute(
                select(User)
                .join(UniversityCell, UniversityCell.leader_user_id == User.id)
                .where(UniversityCell.region_id.in_(region_ids), User.is_active.is_(True))
            )
            for u in result.scalars().all():
                people[u.id] = u
        result = await session.execute(select(User).where(or_(User.role == ROLE_FEDERAL, User.is_federal.is_(True)), User.is_active.is_(True)))
        for u in result.scalars().all():
            people[u.id] = u
        people.pop(user.id, None)
        return sorted(people.values(), key=lambda u: u.full_name)

    if user.role == ROLE_CELL_LEADER:
        # Руководитель ячейки пишет вверх: руководителю своего региона.
        cell = await actor_cell(session, user)
        if cell is None:
            return []
        region = await session.get(Region, cell.region_id)
        if region is None or region.leader_user_id is None:
            return []
        leader = await session.get(User, region.leader_user_id)
        return [leader] if leader and leader.is_active else []

    return []


def require_same_cell(cell: UniversityCell | None, object_cell_id: int | None) -> None:
    """Доп. слой поверх require_edit(region_id) для PATCH/DELETE/по-объектных GET
    в members/events/finance: региональные роли (leader и выше) правят весь
    регион, а руководителю ячейки — только объекты собственной ячейки."""
    if cell is not None and object_cell_id != cell.id:
        raise AccessDenied("Доступно только для своей вузовской ячейки")


async def can_message(session: AsyncSession, user: User, recipient_id: int) -> bool:
    return any(u.id == recipient_id for u in await correspondents(session, user))

