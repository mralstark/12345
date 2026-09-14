"""Бизнес-логика создания регионов и пользователей — общая для CLI
(scripts/admin.py) и веб-админ-панели (api/routers/regions.py), чтобы не
дублировать её в двух местах. Здесь только чистые async-функции без побочного вывода:
конфликт (регион уже есть, федеральный уже назначен и т.п.) — ValueError с
понятным текстом, разбирается вызывающим (CLI печатает, бот шлёт сообщение).

Управленческая роль назначается только уже существующему аккаунту (человек
прошёл саморегистрацию, telegram_id уже проставлен) — см. _resolve_or_promote."""

import logging
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import STORAGE_DIR
from database.models import (
    ROLE_CELL_LEADER,
    ROLE_COORDINATOR,
    ROLE_FEDERAL,
    ROLE_LEADER,
    ROLE_PARTICIPANT,
    ROLE_SUPERUSER,
    BirthdayNotice,
    BureauMember,
    Category,
    CoordinatorRegion,
    Document,
    Event,
    EventAttendance,
    EventTaskAssignee,
    Keyword,
    Member,
    MemberQuestProgress,
    MembershipApplication,
    NewsComment,
    NewsPhoto,
    NewsPost,
    NewsReaction,
    NewsView,
    Region,
    ShopPurchase,
    Task,
    Transaction,
    University,
    UniversityCell,
    User,
)
from utils.invites import generate_application_code

logger = logging.getLogger(__name__)


def _stored_file(relative_path: str | None) -> Path | None:
    if not relative_path:
        return None
    root = STORAGE_DIR.resolve()
    target = (root / relative_path).resolve()
    return target if target != root and root in target.parents else None


def _delete_stored_files(paths: set[Path]) -> None:
    for target in paths:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            logger.exception("Не удалось удалить файл хранилища: %s", target)


async def _resolve_or_promote(session: AsyncSession, role: str, member_id: int) -> User:
    """Общий шаг всех create_*: назначение управленческой роли — это всегда
    повышение уже существующего аккаунта (человек сам прошёл саморегистрацию,
    services/applications.py::approve_application уже проставил telegram_id),
    никогда не создание нового с нуля. Если у человека из «Состава» ещё нет
    аккаунта — ему сначала нужно самому пройти регистрацию по ссылке региона
    (Region.application_code), потом ему назначают роль."""
    member = await session.get(Member, member_id)
    if member is None:
        raise ValueError("Человек не найден в составе")

    existing = (await session.execute(select(User).where(User.member_id == member_id))).scalar_one_or_none()
    if existing is None:
        raise ValueError(
            f"У «{member.full_name}» ещё нет аккаунта — попросите его сначала пройти "
            "самостоятельную регистрацию по ссылке региона, затем назначьте роль."
        )
    if existing.role == ROLE_SUPERUSER:
        raise ValueError("Нельзя назначить управленческую роль техническому superuser")
    existing.role = role
    await session.commit()
    await session.refresh(existing)
    return existing


async def create_region(session: AsyncSession, name: str, genitive: str | None = None) -> Region:
    existing = (await session.execute(select(Region).where(Region.name == name))).scalar_one_or_none()
    if existing is not None:
        raise ValueError(f"Регион «{name}» уже существует")

    # application_code сразу — без него не работает многоразовая ссылка на
    # форму заявки о вступлении (handlers/apply.py).
    region = Region(name=name, genitive_name=genitive, application_code=generate_application_code())
    session.add(region)
    await session.commit()
    await session.refresh(region)
    return region


async def regenerate_application_code(session: AsyncSession, region_id: int) -> Region:
    """При утечке многоразовой ссылки — старая перестаёт работать сразу."""
    region = await session.get(Region, region_id)
    if region is None:
        raise ValueError("Регион не найден")
    region.application_code = generate_application_code()
    await session.commit()
    await session.refresh(region)
    return region


async def create_leader(
    session: AsyncSession,
    region_id: int,
    *,
    member_id: int,
    phone: str | None = None,
) -> User:
    """Человек должен быть уже саморегистрирован (см. _resolve_or_promote) —
    это просто повышение роли. Замену текущего руководителя (если был)
    выполняет без вопросов — решение спрашивать подтверждение остаётся за
    вызывающим, у которого есть доступ к региону ДО вызова (см.
    api/routers/regions.py — там это явный шаг в веб-форме)."""
    region = await session.get(Region, region_id)
    if region is None:
        raise ValueError("Регион не найден")

    previous_id = region.leader_user_id

    user = await _resolve_or_promote(session, ROLE_LEADER, member_id)
    if phone:
        user.phone = phone
    region.leader_user_id = user.id
    await session.flush()

    # Прежний руководитель иначе оставался бы с ролью, но без региона: его
    # управленческий кабинет показывал бы пустоту, потому что доступные
    # регионы у ROLE_LEADER ищутся как раз по этому полю.
    if previous_id is not None and previous_id != user.id:
        await _demote_if_nothing_left(session, previous_id)

    await session.commit()
    await session.refresh(user)
    return user


async def _demote_if_nothing_left(session: AsyncSession, user_id: int) -> User | None:
    """Снимает управленческую роль, если человеку больше нечем управлять.

    Роль без объекта управления — это не «почётное звание», а сломанный
    кабинет: у руководителя отделения доступные регионы ищутся по
    Region.leader_user_id, у координатора — по CoordinatorRegion, у
    руководителя ячейки — по UniversityCell.leader_user_id (utils/access::
    actor_cell). Не осталось ни одного — возвращаем человека в участники.
    """
    person = await session.get(User, user_id)
    if person is None or person.role not in (ROLE_LEADER, ROLE_COORDINATOR, ROLE_CELL_LEADER):
        return None

    leads = (
        await session.execute(select(Region.id).where(Region.leader_user_id == user_id))
    ).scalars().first()
    coordinates = (
        await session.execute(
            select(CoordinatorRegion.id).where(CoordinatorRegion.coordinator_user_id == user_id)
        )
    ).scalars().first()
    cells = (
        await session.execute(select(UniversityCell.id).where(UniversityCell.leader_user_id == user_id))
    ).scalars().first()
    if leads is not None or coordinates is not None or cells is not None:
        return None

    person.role = ROLE_PARTICIPANT
    return person


async def remove_leader(session: AsyncSession, region_id: int) -> User | None:
    """Снимает руководителя с региона. Возвращает того, кого сняли, или None,
    если руководителя не было."""
    region = await session.get(Region, region_id)
    if region is None:
        raise ValueError("Регион не найден")
    if region.leader_user_id is None:
        return None

    person = await session.get(User, region.leader_user_id)
    region.leader_user_id = None
    await session.flush()
    await _demote_if_nothing_left(session, person.id if person else 0)
    await session.commit()
    if person is not None:
        await session.refresh(person)
    return person


async def remove_coordinator(session: AsyncSession, user_id: int) -> User | None:
    """Снимает координатора со всех его регионов."""
    person = await session.get(User, user_id)
    if person is None:
        raise ValueError("Человек не найден")
    await session.execute(
        delete(CoordinatorRegion).where(CoordinatorRegion.coordinator_user_id == user_id)
    )
    await session.flush()
    await _demote_if_nothing_left(session, user_id)
    await session.commit()
    await session.refresh(person)
    return person


async def remove_cell_leader(session: AsyncSession, cell_id: int) -> User | None:
    """Снимает руководителя с вузовской ячейки — так же, как remove_leader
    снимает с отделения. Раньше руководителя можно было только сменить: если
    он выпустился или ушёл, поставить на его место было некого, а убрать
    нельзя, и ячейка навсегда оставалась с бывшим во главе."""
    cell = await session.get(UniversityCell, cell_id)
    if cell is None:
        raise ValueError("Ячейка не найдена")
    if cell.leader_user_id is None:
        return None

    person = await session.get(User, cell.leader_user_id)
    cell.leader_user_id = None
    await session.flush()
    await _demote_if_nothing_left(session, person.id if person else 0)
    await session.commit()
    if person is not None:
        await session.refresh(person)
    return person


async def create_cell_leader(
    session: AsyncSession,
    cell_id: int,
    *,
    member_id: int,
    phone: str | None = None,
) -> User:
    cell = await session.get(UniversityCell, cell_id)
    if cell is None:
        raise ValueError("Вузовская ячейка не найдена")

    user = await _resolve_or_promote(session, ROLE_CELL_LEADER, member_id)
    if phone:
        user.phone = phone
    cell.leader_user_id = user.id
    await session.commit()
    await session.refresh(user)
    return user


async def create_coordinator(
    session: AsyncSession,
    region_ids: list[int],
    *,
    member_id: int,
    phone: str | None = None,
) -> User:
    if not region_ids:
        raise ValueError("Нужен хотя бы один регион")

    user = await _resolve_or_promote(session, ROLE_COORDINATOR, member_id)
    if phone:
        user.phone = phone
    await session.flush()

    for region_id in region_ids:
        region = await session.get(Region, region_id)
        if region is None:
            continue
        # Один регион — один координатор (ТЗ §3): прежняя привязка снимается.
        existing_link = (
            await session.execute(select(CoordinatorRegion).where(CoordinatorRegion.region_id == region_id))
        ).scalar_one_or_none()
        if existing_link is not None:
            await session.delete(existing_link)
            await session.flush()
        session.add(CoordinatorRegion(coordinator_user_id=user.id, region_id=region_id))

    await session.commit()
    await session.refresh(user)
    return user


async def create_federal(
    session: AsyncSession,
    *,
    member_id: int,
    phone: str | None = None,
) -> User:
    """Федеральных координаторов может быть несколько одновременно (план
    «Убираем технического superuser» — админ-доступ и «войти как» даются не
    одному техническому аккаунту, а нескольким доверенным людям), поэтому
    уникальность роли больше не проверяется — раньше здесь был запрет на
    второго федерального, сняли сознательно."""
    user = await _resolve_or_promote(session, ROLE_FEDERAL, member_id)
    if phone:
        user.phone = phone
        await session.commit()
        await session.refresh(user)
    return user


# --- Редактирование и архивирование ------------------------------------------
# Обратимо, а не физическое удаление: у региона/пользователя слишком много
# зависимых записей (состав, финансы, письма, задачи), чтобы стирать их
# каскадом по одному тапу в чате. Region.is_active/User.is_active уже
# используются фильтрами доступа (utils/access.py) — ничего нового в схеме.


async def update_region(session: AsyncSession, region_id: int, name: str | None = None, genitive: str | None = None) -> Region:
    region = await session.get(Region, region_id)
    if region is None:
        raise ValueError("Регион не найден")
    if name and name != region.name:
        clash = (await session.execute(select(Region).where(Region.name == name, Region.id != region_id))).scalar_one_or_none()
        if clash is not None:
            raise ValueError(f"Регион «{name}» уже существует")
        region.name = name
    if genitive is not None:
        region.genitive_name = genitive or None
    await session.commit()
    await session.refresh(region)
    return region


async def archive_region(session: AsyncSession, region_id: int) -> Region:
    """Закрывает регион и его приглашение без необратимого удаления данных."""
    region = await session.get(Region, region_id)
    if region is None:
        raise ValueError("Регион не найден")
    region.is_active = False
    region.application_code = None
    await session.commit()
    await session.refresh(region)
    return region


async def unarchive_region(session: AsyncSession, region_id: int) -> Region:
    region = await session.get(Region, region_id)
    if region is None:
        raise ValueError("Регион не найден")
    region.is_active = True
    if not region.application_code:
        region.application_code = generate_application_code()
    await session.commit()
    await session.refresh(region)
    return region


async def update_user(session: AsyncSession, user_id: int, full_name: str | None = None, phone: str | None = None) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise ValueError("Пользователь не найден")
    if full_name:
        user.full_name = full_name
    if phone is not None:
        user.phone = phone or None
    await session.commit()
    await session.refresh(user)
    return user


async def deactivate_user(session: AsyncSession, user_id: int) -> User:
    """То же, что scripts/admin.py::cmd_user_deactivate делал раньше сам —
    вынесено сюда, чтобы бот и CLI не расходились в поведении."""
    user = await session.get(User, user_id)
    if user is None:
        raise ValueError("Пользователь не найден")
    user.is_active = False
    await session.commit()
    await session.refresh(user)
    return user


async def reactivate_user(session: AsyncSession, user_id: int) -> User:
    """Активирует запись обратно — telegram_id (если был) остаётся как есть."""
    user = await session.get(User, user_id)
    if user is None:
        raise ValueError("Пользователь не найден")
    user.is_active = True
    await session.commit()
    await session.refresh(user)
    return user


# --- Исключение из состава ---------------------------------------------------
# «Исключить» — одно и то же действие для человека с личным кабинетом и без
# (план §5, не два уровня строгости): полное, необратимое физическое удаление.
# Тот же паттерн FK-зачистки, что уже был один раз проделан вручную скриптом
# при переходе на самостоятельную регистрацию — здесь оформлен как
# переиспользуемая функция для одного человека вместо разового скрипта.


# Каждая ссылка на человека из схемы — здесь. Список сверяется со схемой
# тестом (tests/test_exclude_member.py): новая таблица со ссылкой на users
# или members не проедет молча, тест упадёт, пока её сюда не впишут.
#
# Так и вышло однажды: исключение падало пятисотой на news_views, потому что
# перечень писался руками и отстал от схемы на пять таблиц. Считать ссылки
# глазами оказалось нельзя — теперь их считает тест.
EXCLUDE_HANDLES_USER_REFS = frozenset({
    "regions.leader_user_id",
    "university_cells.leader_user_id",
    "users.view_as_user_id",
    "bureau_members.user_id",
    "coordinator_regions.coordinator_user_id",
    "membership_applications.reviewed_by_user_id",
    "news_posts.author_user_id",
    "news_comments.author_user_id",
    "news_reactions.user_id",
    "news_views.user_id",
    "tasks.from_user_id",
    "tasks.to_user_id",
    "documents.author_id",
    "transactions.author_id",
})

EXCLUDE_HANDLES_MEMBER_REFS = frozenset({
    "users.member_id",
    "birthday_notices.member_id",
    "events.responsible_member_id",
    "member_quest_progress.member_id",
    "shop_purchases.member_id",
    "event_attendance.member_id",
    "event_task_assignees.member_id",
})


async def _delete_all(session: AsyncSession, model, condition) -> None:
    for row in (await session.execute(select(model).where(condition))).scalars().all():
        await session.delete(row)


async def exclude_member(
    session: AsyncSession, member_id: int, *, commit: bool = True
) -> set[Path]:
    member = await session.get(Member, member_id)
    if member is None:
        raise ValueError("Человек не найден в составе")

    stored_files: set[Path] = set()
    avatar = _stored_file(member.avatar_path)
    if avatar is not None:
        stored_files.add(avatar)

    # Ссылки на самого человека из «Состава» — не зависят от того, есть ли у
    # него личный кабинет: оба поля смотрят на Member напрямую, не на User.
    for event in (await session.execute(select(Event).where(Event.responsible_member_id == member_id))).scalars().all():
        event.responsible_member_id = None
    # Всё, что привязано к карточке в «Составе», а не к учётной записи:
    # уходит вместе с человеком, оставлять эти строки не за кем.
    await _delete_all(session, BirthdayNotice, BirthdayNotice.member_id == member_id)
    await _delete_all(session, MemberQuestProgress, MemberQuestProgress.member_id == member_id)
    await _delete_all(session, ShopPurchase, ShopPurchase.member_id == member_id)
    await _delete_all(session, EventAttendance, EventAttendance.member_id == member_id)
    await _delete_all(session, EventTaskAssignee, EventTaskAssignee.member_id == member_id)

    user = (await session.execute(select(User).where(User.member_id == member_id))).scalar_one_or_none()
    if user is not None:
        for tx in (await session.execute(select(Transaction).where(Transaction.author_id == user.id))).scalars().all():
            tx.author_id = None
        for doc in (await session.execute(select(Document).where(Document.author_id == user.id))).scalars().all():
            doc.author_id = None
        for app in (
            await session.execute(
                select(MembershipApplication).where(MembershipApplication.reviewed_by_user_id == user.id)
            )
        ).scalars().all():
            app.reviewed_by_user_id = None

        # Кто-то мог смотреть систему его глазами (handlers/impersonate.py) —
        # иначе удаление упрётся в чужую строку.
        for viewer in (
            await session.execute(select(User).where(User.view_as_user_id == user.id))
        ).scalars().all():
            viewer.view_as_user_id = None

        # NOT NULL поля — оставить не с кем, удаляем сами записи, а не обнуляем.
        await _delete_all(session, BureauMember, BureauMember.user_id == user.id)
        await _delete_all(session, NewsComment, NewsComment.author_user_id == user.id)
        await _delete_all(session, NewsReaction, NewsReaction.user_id == user.id)
        await _delete_all(session, NewsView, NewsView.user_id == user.id)
        for task in (
            await session.execute(select(Task).where((Task.from_user_id == user.id) | (Task.to_user_id == user.id)))
        ).scalars().all():
            await session.delete(task)
        # NewsPost.author_user_id — NOT NULL, как Task выше; удаление каскадом
        # заберёт и адресатов-регионы этой новости
        # (cascade="all, delete-orphan" на NewsPost.regions).
        posts = (
            await session.execute(select(NewsPost).where(NewsPost.author_user_id == user.id))
        ).scalars().all()
        post_ids = [post.id for post in posts]
        if post_ids:
            for stored_path in (
                await session.execute(select(NewsPhoto.stored_path).where(NewsPhoto.post_id.in_(post_ids)))
            ).scalars().all():
                target = _stored_file(stored_path)
                if target is not None:
                    stored_files.add(target)
        for post in posts:
            await session.delete(post)

        for region in (await session.execute(select(Region).where(Region.leader_user_id == user.id))).scalars().all():
            region.leader_user_id = None
        for cell in (await session.execute(select(UniversityCell).where(UniversityCell.leader_user_id == user.id))).scalars().all():
            cell.leader_user_id = None
        for link in (
            await session.execute(select(CoordinatorRegion).where(CoordinatorRegion.coordinator_user_id == user.id))
        ).scalars().all():
            await session.delete(link)

        await session.flush()
        await session.delete(user)

    await session.flush()
    await session.delete(member)
    if commit:
        await session.commit()
        _delete_stored_files(stored_files)
    return stored_files


# --- Удаление региона --------------------------------------------------------
# Тоже одно необратимое действие, не архивирование (план «Убираем технического
# superuser», кабинет федерального координатора — api/routers/regions.py):
# тот же принцип, что и у exclude_member — «Удалить» значит удалить всё,
# без мягкого промежуточного варианта.


async def delete_region_permanently(session: AsyncSession, region_id: int) -> None:
    region = await session.get(Region, region_id)
    if region is None:
        raise ValueError("Регион не найден")

    # Каждый человек состава — тем же путём, что и «Исключить» одного
    # человека: так же полностью убирает и его личный кабинет, если был,
    # без повторения той же FK-логики здесь ещё раз.
    stored_files: set[Path] = set()
    member_ids = (await session.execute(select(Member.id).where(Member.region_id == region_id))).scalars().all()
    for member_id in member_ids:
        stored_files.update(await exclude_member(session, member_id, commit=False))

    # Остальное — то, что не привязано к конкретному человеку из состава.
    await session.execute(delete(MembershipApplication).where(MembershipApplication.region_id == region_id))

    event_ids = select(Event.id).where(Event.region_id == region_id)
    category_ids = select(Category.id).where(Category.region_id == region_id)
    await session.execute(delete(EventAttendance).where(EventAttendance.event_id.in_(event_ids)))
    await session.execute(delete(Keyword).where(Keyword.category_id.in_(category_ids)))
    for stored_path in (
        await session.execute(select(Document.stored_path).where(Document.region_id == region_id))
    ).scalars().all():
        target = _stored_file(stored_path)
        if target is not None:
            stored_files.add(target)
    await session.execute(delete(Document).where(Document.region_id == region_id))
    await session.execute(delete(Transaction).where(Transaction.region_id == region_id))
    await session.execute(delete(Event).where(Event.region_id == region_id))
    await session.execute(delete(Category).where(Category.region_id == region_id))
    await session.execute(delete(UniversityCell).where(UniversityCell.region_id == region_id))
    await session.execute(delete(CoordinatorRegion).where(CoordinatorRegion.region_id == region_id))

    # Каталог вузов общий на всю систему — отвязываем от удаляемого региона,
    # а не удаляем сами записи (University.region_id nullable ровно за этим).
    for university in (await session.execute(select(University).where(University.region_id == region_id))).scalars().all():
        university.region_id = None

    await session.flush()
    await session.delete(region)
    await session.commit()
    _delete_stored_files(stored_files)
