"""Разбор дат, форматирование, отчётные периоды, повторы мероприятий."""

from datetime import date

from services.recurring_events import next_occurrence
from utils.parser import first_and_patronymic, format_kopecks, parse_date_hint, parse_deadline
from utils.period import resolve_period


def test_first_and_patronymic():
    assert first_and_patronymic("Иванов Иван Иванович") == "Иван Иванович"
    assert first_and_patronymic("Иванов Иван") == "Иван"
    assert first_and_patronymic("Иванов") == "Иванов"


def test_parse_date_hint_looks_back():
    today = date(2026, 7, 27)
    assert parse_date_hint("вчера", today) == date(2026, 7, 26)
    assert parse_date_hint("21", today) == date(2026, 7, 21)
    # Дата без года, которая ещё не наступила, относится к прошлому году.
    assert parse_date_hint("26.12", today) == date(2025, 12, 26)


def test_parse_deadline_looks_forward():
    today = date(2026, 7, 27)
    assert parse_deadline("завтра", today) == date(2026, 7, 28)
    assert parse_deadline("31.12", today) == date(2026, 12, 31)
    # Прошедшая в этом году дата означает следующий год.
    assert parse_deadline("01.02", today) == date(2027, 2, 1)
    assert parse_deadline("ерунда", today) is None


def test_format_kopecks():
    assert format_kopecks(250000) == "2 500"
    assert format_kopecks(250050) == "2 500,50"
    assert format_kopecks(-100) == "-1"


def test_resolve_period_calendar():
    today = date(2026, 7, 27)
    start, end, label = resolve_period("month", 0, today)
    assert (start, end) == (date(2026, 7, 1), date(2026, 7, 31))
    assert label == "Июль 2026"

    start, end, _ = resolve_period("month", -1, today)
    assert (start, end) == (date(2026, 6, 1), date(2026, 6, 30))

    start, end, label = resolve_period("semester", 0, today)
    assert (start, end) == (date(2026, 7, 16), date(2026, 12, 31))
    assert label == "2 семестр 2026"

    start, end, label = resolve_period("semester", -1, today)
    assert (start, end) == (date(2026, 1, 1), date(2026, 7, 15))
    assert label == "1 семестр 2026"

    start, end, _ = resolve_period("year", 0, today)
    assert (start, end) == (date(2026, 1, 1), date(2026, 12, 31))


def test_next_occurrence():
    assert next_occurrence(date(2026, 7, 27), "weekly") == date(2026, 8, 3)
    assert next_occurrence(date(2026, 7, 27), "biweekly") == date(2026, 8, 10)
    assert next_occurrence(date(2026, 7, 27), "monthly") == date(2026, 8, 27)
    # Декабрь перекатывается в следующий год.
    assert next_occurrence(date(2026, 12, 15), "monthly") == date(2027, 1, 15)
    # 31 января + месяц: день подрезается по длине февраля.
    assert next_occurrence(date(2026, 1, 31), "monthly") == date(2026, 2, 28)


# --- Подпись роли -------------------------------------------------------------


async def test_participant_has_no_role_caption(session, world):
    """Роль участника — внутренняя: она значит «управленческих прав нет» и
    человеку ничего не сообщает. Хуже того, ярлык «Участник Братства» спорил
    со статусом: у активиста выходило, будто он уже посвящён."""
    from database.models import ROLE_PARTICIPANT, Member, User
    from utils.roles import role_label

    member = Member(region_id=world["moscow"].id, full_name="Иванов Иван")
    session.add(member)
    await session.flush()
    user = User(full_name="Иванов Иван", role=ROLE_PARTICIPANT, telegram_id=7001, member_id=member.id)
    session.add(user)
    await session.commit()

    assert await role_label(session, user) is None


async def test_cell_leader_caption_has_no_case_to_decline(session, world):
    """«Руководитель Академистов {вуз}» требовал родительного падежа, ради
    которого у ячейки было отдельное поле genitive_name — его приходилось
    склонять руками при каждом заведении. После слова «ячейки» название стоит
    в именительном, и склонять нечего."""
    from database.models import ROLE_CELL_LEADER, University, UniversityCell, User
    from utils.roles import role_label

    university = University(name="СПбГУ")
    session.add(university)
    await session.flush()
    user = User(full_name="Кузнецов Иван", role=ROLE_CELL_LEADER, telegram_id=7301)
    session.add(user)
    await session.flush()
    session.add(UniversityCell(region_id=world["moscow"].id, university_id=university.id,
                               name="СПбГУ", genitive_name="СПбГУ", leader_user_id=user.id))
    await session.commit()

    assert await role_label(session, user) == "Руководитель ячейки СПбГУ"


async def test_leaders_keep_their_caption(session, world):
    """Убрали подпись только у участника: у руководителя она несёт смысл."""
    from utils.roles import role_label

    assert await role_label(session, world["leader_moscow"]) == "Руководитель — Академисты | Москва"
    assert await role_label(session, world["federal"]) == "Федеральный координатор"
