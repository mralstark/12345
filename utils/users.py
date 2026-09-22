"""Поиск и создание пользователей — общее для бота и API."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import SUPERUSER_TELEGRAM_IDS
from database.models import (
    ROLE_CELL_LEADER,
    ROLE_COORDINATOR,
    ROLE_LEADER,
    ROLE_PARTICIPANT,
    ROLE_SUPERUSER,
    CoordinatorRegion,
    Member,
    Region,
    UniversityCell,
    User,
)
from utils.tz import now
from utils.permissions import has_role

# «Войти как» — диагностическая возможность технического superuser. Federal
# имеет широкий обзор, но не права записи; подмена его объектом руководителя
# незаметно повышала полномочия до роли цели.
IMPERSONATOR_ROLES = (ROLE_SUPERUSER,)


async def get_user_by_telegram_id(session: AsyncSession, telegram_id: int) -> User | None:
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    return result.scalar_one_or_none()


async def ensure_superuser(session: AsyncSession, telegram_id: int, full_name: str) -> User | None:
    """Telegram ID из SUPERUSER_TELEGRAM_IDS получает доступ без приглашения —
    иначе первого человека в пустой системе некому пригласить."""
    if telegram_id not in SUPERUSER_TELEGRAM_IDS:
        return None

    user = await get_user_by_telegram_id(session, telegram_id)
    if user:
        if not user.is_superuser:
            user.is_superuser = True
            if user.role == ROLE_PARTICIPANT:
                user.role = ROLE_SUPERUSER
            await session.commit()
        return user

    user = User(
        telegram_id=telegram_id,
        full_name=full_name or f"Superuser {telegram_id}",
        role=ROLE_SUPERUSER,
        is_superuser=True,
        activated_at=now().replace(tzinfo=None),
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def resolve_user(session: AsyncSession, telegram_id: int, full_name: str = "") -> User | None:
    """Пользователь по telegram_id; для технических superuser'ов создаётся на лету.

    Единая точка входа и для бота, и для API (api/auth.py::get_current_user) —
    поэтому режим «войти как» подключается ровно здесь: если реальный
    пользователь вправе им пользоваться (IMPERSONATOR_ROLES) и у него непустой
    view_as_user_id, возвращаем ЦЕЛЬ вместо него самого. Реальная личность не
    теряется — она кладётся во временный Python-атрибут _impersonated_by (не
    персистится, не поле модели), по нему бот и веб рисуют баннер «Вы смотрите
    как...» и кнопку выхода.
    """
    # Сначала спрашиваем про SUPERUSER_TELEGRAM_IDS, а не после того, как
    # обычный поиск ничего не нашёл. Раньше было наоборот, и ветка «повысить
    # того, кто уже есть» внутри ensure_superuser не срабатывала никогда:
    # у человека с учётной записью до неё не доходило. Из-за этого вписать в
    # .env участника, который уже зарегистрирован, было бесполезно — админом
    # он не становился, а .env обещал обратное.
    user = await ensure_superuser(session, telegram_id, full_name)
    if user is None:
        user = await get_user_by_telegram_id(session, telegram_id)
    if user is None:
        return None

    if has_role(user, ROLE_SUPERUSER) and user.view_as_user_id is not None:
        target = await session.get(User, user.view_as_user_id)
        if target is not None and target.is_active:
            target._impersonated_by = user  # type: ignore[attr-defined]
            return target
        # Цель удалена/деактивирована — тихо сбрасываем, чтобы режим не залипал.
        user.view_as_user_id = None
        await session.commit()

    return user


async def impersonation_candidates(session: AsyncSession, group: str) -> list[User]:
    """Активные пользователи заданной группы — для картинки-выбора в боте
    («Войти как...» → сперва группа, потом конкретный человек, см.
    handlers/impersonate.py). group — либо роль как есть («leader»,
    «coordinator», «cell_leader», «federal»), либо «status:<статус>»
    (activist/member/alumni) — обычные участники все имеют role=participant,
    поэтому там группа не по роли, а по статусу их записи в «Составе»."""
    kind, _, value = group.partition(":")
    if kind == "status":
        result = await session.execute(
            select(User)
            .join(Member, Member.id == User.member_id)
            .where(User.role == ROLE_PARTICIPANT, Member.status == value, User.is_active.is_(True))
            .order_by(User.full_name)
        )
        return list(result.scalars().all())

    result = await session.execute(
        select(User).where(User.role == group, User.is_active.is_(True)).order_by(User.full_name)
    )
    return list(result.scalars().all())


async def candidate_context(session: AsyncSession, user: User) -> str:
    """Короткая подпись под именем в списке выбора: регион/ячейка этого человека."""
    if user.role == ROLE_LEADER:
        region = (await session.execute(select(Region).where(Region.leader_user_id == user.id))).scalar_one_or_none()
        return region.name if region else "регион не назначен"
    if user.role == ROLE_CELL_LEADER:
        cell = (
            await session.execute(select(UniversityCell).where(UniversityCell.leader_user_id == user.id))
        ).scalar_one_or_none()
        return cell.name if cell else "ячейка не назначена"
    if user.role == ROLE_COORDINATOR:
        names = (
            await session.execute(
                select(Region.name)
                .join(CoordinatorRegion, CoordinatorRegion.region_id == Region.id)
                .where(CoordinatorRegion.coordinator_user_id == user.id)
                .order_by(Region.name)
            )
        ).scalars().all()
        return ", ".join(names) if names else "регионы не назначены"
    if user.role == ROLE_PARTICIPANT:
        member = await session.get(Member, user.member_id) if user.member_id else None
        if member is None:
            return "без региона"
        region = await session.get(Region, member.region_id)
        return region.name if region else "регион не назначен"
    return "федеральный"


async def start_impersonation(session: AsyncSession, superuser: User, target_user_id: int) -> User:
    """Включает режим «войти как». Возвращает цель — или бросает ValueError,
    если id не существует / неактивен / указывает на другого superuser'а
    (входить «как superuser» бессмысленно — им и так является сам вызывающий)."""
    if not has_role(superuser, ROLE_SUPERUSER):
        raise ValueError("Режим «Войти как» доступен только техническому superuser")
    target = await session.get(User, target_user_id)
    if target is None or not target.is_active:
        raise ValueError("Пользователь не найден или отключён")
    if has_role(target, ROLE_SUPERUSER):
        raise ValueError("Нельзя войти как другой technical superuser")

    superuser.view_as_user_id = target.id
    await session.commit()
    return target


async def stop_impersonation(session: AsyncSession, superuser: User) -> None:
    superuser.view_as_user_id = None
    await session.commit()
