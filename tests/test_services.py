"""Поведение сервисов: подбор категории, повторы, уведомления о днях рождения."""

from datetime import date, timedelta

from sqlalchemy import func, select

from database.models import (
    EVENT_STATUS_PLANNED,
    MEMBER_STATUS_ACTIVIST,
    ROLE_PARTICIPANT,
    BirthdayNotice,
    Event,
    EventAttendance,
    Member,
    Region,
    Task,
    User,
)
from services.education import bump_courses
from services.finance import create_transaction, ensure_default_categories, find_category_by_hint
from services.notifier import check_birthdays, check_deadlines, check_event_reminders, check_overdue
from services.recurring_events import materialize_event
from services.tasks import create_task, effective_status
from utils.balance_calc import get_balance, get_event_fact
from utils.tz import now as tz_now
from utils.tz import today as tz_today


async def test_default_categories_created_once(session, world):
    region_id = world["moscow"].id
    await ensure_default_categories(session, region_id)
    first = (await session.execute(select(func.count()).select_from(Region))).scalar()
    await ensure_default_categories(session, region_id)
    assert first == 2  # регионы не размножились
    from database.models import Category

    count = (
        await session.execute(select(func.count(Category.id)).where(Category.region_id == region_id))
    ).scalar()
    assert count == 10  # ровно набор из database/defaults.py, без дублей


async def test_find_category_by_keyword_and_name(session, world):
    region_id = world["moscow"].id
    await ensure_default_categories(session, region_id)

    by_keyword = await find_category_by_hint(session, region_id, "бумага")
    assert by_keyword.name == "Канцелярия"

    # Название категории в другом регистре тоже находится (кириллица!).
    by_name = await find_category_by_hint(session, region_id, "ТРАНСПОРТ")
    assert by_name.name == "Транспорт"

    # Ключевое слово внутри фразы.
    inside = await find_category_by_hint(session, region_id, "аренда зала для форума")
    assert inside.name == "Мероприятия"

    assert await find_category_by_hint(session, region_id, "нечто неизвестное") is None


async def test_balance_belongs_to_region(session, world):
    moscow, tula = world["moscow"], world["tula"]
    await create_transaction(session, moscow.id, 100000, "income")
    await create_transaction(session, moscow.id, 40000, "expense")
    await create_transaction(session, tula.id, 700000, "income")

    assert await get_balance(session, moscow.id) == 60000
    assert await get_balance(session, tula.id) == 700000


async def test_event_fact_counts_only_tagged_expenses(session, world):
    region_id = world["moscow"].id
    event = Event(region_id=region_id, title="Форум", date=date(2026, 8, 1), planned_budget=500000)
    session.add(event)
    await session.commit()
    await session.refresh(event)

    await create_transaction(session, region_id, 200000, "expense", event_id=event.id)
    await create_transaction(session, region_id, 90000, "expense")  # без метки — мимо факта
    await create_transaction(session, region_id, 50000, "income", event_id=event.id)  # доход не расход

    assert await get_event_fact(session, event.id) == 200000


async def test_materialize_is_idempotent(session, world):
    template = Event(
        region_id=world["moscow"].id,
        title="Планёрка",
        date=date(2026, 8, 3),
        status=EVENT_STATUS_PLANNED,
        is_recurring=True,
        recurrence_rule="weekly",
        recurrence_until=date(2026, 8, 31),
    )
    session.add(template)
    await session.commit()
    await session.refresh(template)

    created_first = await materialize_event(session, template, today=date(2026, 8, 1))
    created_again = await materialize_event(session, template, today=date(2026, 8, 1))

    assert len(created_first) == 4
    assert created_again == []


async def test_bump_courses_is_idempotent_and_clears_course_without_touching_status(session, world):
    student = Member(
        region_id=world["moscow"].id, full_name="Студентов Курс Второй",
        course=2, study_years=4, course_bumped_year=2025,
    )
    graduate = Member(
        region_id=world["moscow"].id, full_name="Выпускников Курс Четвёртый",
        course=4, study_years=4, course_bumped_year=2025,
    )
    session.add_all([student, graduate])
    await session.commit()

    assert await bump_courses(session, today=date(2026, 9, 1)) == 2
    await session.refresh(student)
    await session.refresh(graduate)
    assert student.course == 3
    assert student.course_bumped_year == 2026
    # Курс обнулился (человек больше не «на каком-то курсе»), но статус и
    # дата выпуска — только руками руководителя, не автоматикой (план:
    # «Выпуск из студенческого Братства» не зависит от окончания вуза).
    assert graduate.course is None
    assert graduate.status == MEMBER_STATUS_ACTIVIST
    assert graduate.alumni_graduated_at is None

    # Повторный запуск в том же учебном году (в т.ч. другим числом) — не сработает,
    # иначе курс продолжал бы расти при каждом цикле нотификатора.
    assert await bump_courses(session, today=date(2026, 9, 5)) == 0
    await session.refresh(student)
    assert student.course == 3


async def test_birthday_notice_sent_once_per_year(session, world):
    today = tz_today()
    session.add(
        Member(
            region_id=world["moscow"].id,
            full_name="Именинник Именинникович",
            birth_date=date(2000, today.month, today.day),
        )
    )
    await session.commit()

    assert await check_birthdays(bot=None) == 1
    # Повторный запуск в тот же день ничего не шлёт — на этом держится перезапуск бота.
    assert await check_birthdays(bot=None) == 0
    assert (await session.execute(select(func.count(BirthdayNotice.id)))).scalar() == 1


async def test_deadline_and_overdue_notifications(session, world):
    today = tz_today()
    coordinator = world["coordinator"]
    leader = world["leader_moscow"]

    await create_task(session, coordinator, leader.id, "Завтрашняя", deadline=today)
    overdue_task = await create_task(
        session, coordinator, leader.id, "Вчерашняя", deadline=today - timedelta(days=3)
    )

    assert await check_deadlines(bot=None) == 1
    assert await check_deadlines(bot=None) == 0  # повторно за тот же день не напоминаем

    # Просрочка уведомляет обе стороны — исполнителя и постановщика.
    assert await check_overdue(bot=None) == 2
    assert await check_overdue(bot=None) == 0

    await session.refresh(overdue_task)
    assert effective_status(overdue_task) == "overdue"


async def test_event_reminder_sent_once_per_day_only_to_those_going(session, world):
    today = tz_today()
    tomorrow = today + timedelta(days=1)
    region_id = world["moscow"].id

    going = Member(region_id=region_id, full_name="Идущий Иван Иванович")
    not_going = Member(region_id=region_id, full_name="Отказавшийся Пётр Петрович")
    session.add_all([going, not_going])
    await session.commit()
    await session.refresh(going)
    await session.refresh(not_going)

    session.add_all([
        User(full_name=going.full_name, role=ROLE_PARTICIPANT, member_id=going.id, telegram_id=2001),
        User(full_name=not_going.full_name, role=ROLE_PARTICIPANT, member_id=not_going.id, telegram_id=2002),
    ])

    event = Event(region_id=region_id, title="Планёрка", date=tomorrow, status=EVENT_STATUS_PLANNED)
    session.add(event)
    await session.commit()
    await session.refresh(event)

    session.add_all([
        EventAttendance(event_id=event.id, member_id=going.id, attended=True),
        EventAttendance(event_id=event.id, member_id=not_going.id, attended=False),
    ])
    await session.commit()

    # Только «иду» получает напоминание — «не иду» не считается.
    assert await check_event_reminders(bot=None) == 1
    # Повторный запуск в тот же день ничего не шлёт — на этом держится перезапуск бота.
    assert await check_event_reminders(bot=None) == 0

    await session.refresh(event)
    assert event.reminder_sent_on == today


async def test_event_reminder_respects_exact_time_not_just_day(session, world):
    """С указанным time напоминание — ровно за 24 часа до него, а не «в
    любой момент дня накануне» (как для мероприятий без времени)."""
    region_id = world["moscow"].id
    going = Member(region_id=region_id, full_name="Идущая Мария")
    session.add(going)
    await session.commit()
    await session.refresh(going)
    session.add(User(full_name=going.full_name, role=ROLE_PARTICIPANT, member_id=going.id, telegram_id=2003))

    now = tz_now()
    too_early = now + timedelta(hours=25)
    due = now + timedelta(hours=23)

    far_event = Event(
        region_id=region_id, title="Далеко", date=too_early.date(), time=too_early.time(), status=EVENT_STATUS_PLANNED
    )
    near_event = Event(
        region_id=region_id, title="Скоро", date=due.date(), time=due.time(), status=EVENT_STATUS_PLANNED
    )
    session.add_all([far_event, near_event])
    await session.commit()
    await session.refresh(far_event)
    await session.refresh(near_event)

    session.add_all([
        EventAttendance(event_id=far_event.id, member_id=going.id, attended=True),
        EventAttendance(event_id=near_event.id, member_id=going.id, attended=True),
    ])
    await session.commit()

    # Только у ближнего мероприятия окно «за 24 часа» уже открылось.
    assert await check_event_reminders(bot=None) == 1
    await session.refresh(near_event)
    await session.refresh(far_event)
    assert near_event.reminder_sent_on == tz_today()
    assert far_event.reminder_sent_on is None


async def test_task_notification_targets_are_users(session, world):
    """Задача создаётся только в пределах разрешённой вертикали."""
    task = await create_task(session, world["coordinator"], world["leader_moscow"].id, "Проверка")
    assert isinstance(task, Task)
    author = await session.get(User, task.from_user_id)
    assert author.id == world["coordinator"].id
