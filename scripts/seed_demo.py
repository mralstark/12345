"""Демо-данные для локальной проверки кабинета.

    python -m scripts.seed_demo

Создаёт два региона с составом, финансами и мероприятиями, плюс руководителей,
координатора и федерального. Пользователям проставляются служебные telegram_id
(900001…900004) — их можно подставить в DEV_TELEGRAM_ID, чтобы открыть Mini App
в обычном браузере без Telegram. Скрипт не трогает уже существующие записи.
"""

import asyncio
import random
from datetime import date, timedelta

from sqlalchemy import func, select

from database.db import async_session, init_db
from database.models import (
    EVENT_STATUS_DONE,
    EVENT_STATUS_PLANNED,
    MEMBER_STATUS_ACTIVIST,
    MEMBER_STATUS_ALUMNI,
    MEMBER_STATUS_MEMBER,
    ROLE_COORDINATOR,
    ROLE_FEDERAL,
    ROLE_LEADER,
    CoordinatorRegion,
    Event,
    EventAttendance,
    Member,
    Region,
    Task,
    University,
    User,
)
from services.education import academic_year
from services.finance import create_transaction, ensure_default_categories, region_categories
from utils.tz import today as tz_today
from utils.university_cells import resolve_member_cell

FIRST_NAMES = ["Иван", "Пётр", "Алексей", "Дмитрий", "Николай", "Сергей", "Андрей", "Михаил", "Егор", "Павел"]
LAST_NAMES = ["Смирнов", "Кузнецов", "Соколов", "Попов", "Лебедев", "Козлов", "Новиков", "Морозов", "Волков", "Зайцев"]

UNIVERSITIES = [
    ("МГУ им. Ломоносова", "Экономический факультет"),
    ("МГТУ им. Баумана", "Факультет машиностроения"),
    ("НИУ ВШЭ", "Факультет права"),
    ("СПбГУ", "Факультет журналистики"),
    ("РАНХиГС", "Факультет госуправления"),
    ("МГИМО", "Факультет международных отношений"),
]
WORKPLACES = ["Сбербанк", "Яндекс", "Правительство региона", "Газпром нефть", "Ростелеком", "Собственное дело"]


async def _get_or_create_region(session, name: str, genitive_name: str | None = None) -> Region:
    region = (await session.execute(select(Region).where(Region.name == name))).scalar_one_or_none()
    if region is None:
        region = Region(name=name, genitive_name=genitive_name)
        session.add(region)
        await session.flush()
    return region


async def _get_or_create_user(session, full_name: str, role: str, telegram_id: int) -> User:
    user = (await session.execute(select(User).where(User.telegram_id == telegram_id))).scalar_one_or_none()
    if user is None:
        user = User(full_name=full_name, role=role, telegram_id=telegram_id)
        session.add(user)
        await session.flush()
    return user


async def main() -> None:
    random.seed(17)
    await init_db()
    today = tz_today()

    async with async_session() as session:
        moscow = await _get_or_create_region(session, "Москва", "Москвы")
        spb = await _get_or_create_region(session, "Санкт-Петербург", "Санкт-Петербурга")

        leader_moscow = await _get_or_create_user(session, "Иванов Иван Иванович", ROLE_LEADER, 900001)
        leader_spb = await _get_or_create_user(session, "Петров Пётр Петрович", ROLE_LEADER, 900002)
        coordinator = await _get_or_create_user(session, "Сидоров Семён Семёнович", ROLE_COORDINATOR, 900003)
        federal = await _get_or_create_user(session, "Фёдоров Фёдор Фёдорович", ROLE_FEDERAL, 900004)

        moscow.leader_user_id = leader_moscow.id
        spb.leader_user_id = leader_spb.id

        for region in (moscow, spb):
            link = (
                await session.execute(select(CoordinatorRegion).where(CoordinatorRegion.region_id == region.id))
            ).scalar_one_or_none()
            if link is None:
                session.add(CoordinatorRegion(coordinator_user_id=coordinator.id, region_id=region.id))
        await session.commit()

        for region, size in ((moscow, 18), (spb, 11)):
            existing = (
                await session.execute(select(func.count(Member.id)).where(Member.region_id == region.id))
            ).scalar() or 0
            if existing:
                continue

            for index in range(size):
                status = random.choice(
                    [MEMBER_STATUS_ACTIVIST, MEMBER_STATUS_MEMBER, MEMBER_STATUS_MEMBER, MEMBER_STATUS_ALUMNI]
                )
                session.add(
                    Member(
                        region_id=region.id,
                        full_name=f"{random.choice(LAST_NAMES)} {random.choice(FIRST_NAMES)}",
                        phone=f"+7 9{random.randint(10, 99)} {random.randint(100, 999)}-{random.randint(10, 99)}-{random.randint(10, 99)}",
                        status=status,
                        # У части людей день рождения на днях — видно в сводке.
                        birth_date=date(2000 + random.randint(0, 6), 1, 1)
                        + timedelta(days=random.randint(0, 364))
                        if index % 4
                        else today.replace(year=2002) + timedelta(days=index),
                    )
                )
            await session.commit()

            await ensure_default_categories(session, region.id)
            categories = await region_categories(session, region.id)
            incomes = [c for c in categories if c.type == "income"]
            expenses = [c for c in categories if c.type == "expense"]

            for _ in range(6):
                await create_transaction(
                    session,
                    region_id=region.id,
                    amount_kopecks=random.randint(3, 40) * 100_00,
                    tx_type="income",
                    category_id=random.choice(incomes).id,
                    author_id=region.leader_user_id,
                    tx_date=today - timedelta(days=random.randint(0, 80)),
                    comment="Взносы за месяц",
                )
            for _ in range(14):
                await create_transaction(
                    session,
                    region_id=region.id,
                    amount_kopecks=random.randint(2, 25) * 100_00,
                    tx_type="expense",
                    category_id=random.choice(expenses).id,
                    author_id=region.leader_user_id,
                    tx_date=today - timedelta(days=random.randint(0, 80)),
                )

            members = list(
                (await session.execute(select(Member).where(Member.region_id == region.id))).scalars().all()
            )
            for offset, title in ((-21, "Отчётное собрание"), (-7, "Лекция об истории Братства"), (9, "Региональный форум")):
                event = Event(
                    region_id=region.id,
                    title=title,
                    date=today + timedelta(days=offset),
                    description="Демонстрационная запись.",
                    responsible_member_id=random.choice(members).id,
                    status=EVENT_STATUS_DONE if offset < 0 else EVENT_STATUS_PLANNED,
                    planned_budget=random.randint(10, 60) * 100_00 if offset > 0 else None,
                )
                session.add(event)
                await session.flush()
                for member in random.sample(members, k=max(3, len(members) // 2)):
                    session.add(EventAttendance(event_id=event.id, member_id=member.id, attended=True))
            await session.commit()

        # Вуз/факультет/курс/место работы — отдельным проходом по ВСЕМ людям региона
        # (а не только свежесозданным), чтобы обогатить уже существующую демо-базу
        # без повторного запуска сидинга с нуля. Трогает только пустые записи.
        # Заодно демонстрирует авто-создание ячейки (utils/university_cells) —
        # несколько человек на один вуз в регионе должны схлопнуться в одну ячейку.
        university_cache: dict[str, University] = {
            u.name.lower(): u for u in (await session.execute(select(University))).scalars().all()
        }

        async def get_or_create_university(name: str) -> University:
            key = name.lower()
            university = university_cache.get(key)
            if university is None:
                university = University(name=name)
                session.add(university)
                await session.flush()
                university_cache[key] = university
            return university

        for region in (moscow, spb):
            region_members = (
                await session.execute(select(Member).where(Member.region_id == region.id))
            ).scalars().all()
            changed = False
            for member in region_members:
                if member.university_id or member.workplace:
                    continue
                if member.status == MEMBER_STATUS_ALUMNI:
                    if random.random() < 0.7:
                        university_name, faculty = random.choice(UNIVERSITIES)
                        university = await get_or_create_university(university_name)
                        member.university_id = university.id
                        member.faculty = faculty
                        member.workplace = random.choice(WORKPLACES)
                        cell = await resolve_member_cell(session, region.id, university.id)
                        member.cell_id = cell.id if cell is not None else None
                        changed = True
                elif random.random() < 0.8:
                    university_name, faculty = random.choice(UNIVERSITIES)
                    university = await get_or_create_university(university_name)
                    study_years = random.choice([4, 4, 4, 5])
                    member.university_id = university.id
                    member.faculty = faculty
                    member.study_years = study_years
                    member.course = random.randint(1, study_years)
                    # Иначе ближайший цикл services.education.bump_courses тут же
                    # повысит курс этому демо-студенту, не дожидаясь 1 сентября.
                    member.course_bumped_year = academic_year(today)
                    cell = await resolve_member_cell(session, region.id, university.id)
                    member.cell_id = cell.id if cell is not None else None
                    changed = True
            if changed:
                await session.commit()

        existing_tasks = (await session.execute(select(func.count(Task.id)))).scalar() or 0
        if not existing_tasks:
            session.add_all(
                [
                    Task(
                        from_user_id=coordinator.id,
                        to_user_id=leader_moscow.id,
                        title="Сдать финансовый отчёт за квартал",
                        text="Выгрузить отчёт из кабинета и прислать координатору.",
                        deadline=today + timedelta(days=5),
                    ),
                    Task(
                        from_user_id=federal.id,
                        to_user_id=leader_spb.id,
                        title="Обновить список состава",
                        deadline=today - timedelta(days=2),
                    ),
                ]
            )
            await session.commit()

    print("Демо-данные готовы.")
    print("telegram_id для DEV_TELEGRAM_ID:")
    print("  900001 — руководитель (Москва)")
    print("  900002 — руководитель (Санкт-Петербург)")
    print("  900003 — координатор регионов")
    print("  900004 — федеральный координатор")


if __name__ == "__main__":
    asyncio.run(main())
