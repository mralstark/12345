"""Федеральное бюро — членство поверх отделения.

Пока это только таблица: в интерфейсе бюро ещё не видно. Проверяем то, что
таблица обязана гарантировать, — иначе следующие шаги будут строиться на
песке.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from database.models import (
    MEMBER_STATUS_ACTIVIST,
    MEMBER_STATUS_ALUMNI,
    MEMBER_STATUS_MEMBER,
    ROLE_PARTICIPANT,
    BureauMember,
    Member,
    Region,
    User,
)
from tests.conftest import login


async def test_bureau_membership_does_not_touch_the_region(session, world):
    """Человек остаётся и в своём отделении, и во главе его: бюро добавляется
    поверх, а не вместо. Ради этого членство и вынесено в отдельную таблицу —
    ничего в составе и в регионах трогать не пришлось."""
    leader = world["leader_moscow"]
    moscow = await session.get(Region, world["moscow"].id)
    assert moscow.leader_user_id == leader.id

    session.add(BureauMember(user_id=leader.id, title="Куратор Сибири"))
    await session.commit()

    await session.refresh(moscow)
    assert moscow.leader_user_id == leader.id, "членство в бюро сместило руководителя отделения"

    in_bureau = (await session.execute(
        select(BureauMember).where(BureauMember.user_id == leader.id)
    )).scalar_one()
    assert in_bureau.title == "Куратор Сибири"


async def test_person_cannot_be_in_bureau_twice(session, world):
    """Одна запись на человека: два раза в бюро состоять нельзя, иначе список
    начнёт двоиться, а должность станет неоднозначной."""
    session.add(BureauMember(user_id=world["federal"].id, title="Руководитель бюро"))
    await session.commit()

    session.add(BureauMember(user_id=world["federal"].id, title="Ещё раз"))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_title_is_required(session, world):
    """Должность обязательна — список бюро должен отвечать на вопрос «кто за
    что», а не только «кто там есть»."""
    session.add(BureauMember(user_id=world["coordinator"].id))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_bureau_keeps_the_given_order(session, world):
    """Порядок задаёт руководство: бюро — не алфавитный справочник, первым
    идёт тот, кто его возглавляет."""
    session.add_all([
        BureauMember(user_id=world["coordinator"].id, title="Секретарь", sort_order=2),
        BureauMember(user_id=world["federal"].id, title="Руководитель бюро", sort_order=1),
    ])
    await session.commit()

    rows = (await session.execute(
        select(BureauMember).order_by(BureauMember.sort_order)
    )).scalars().all()
    assert [r.title for r in rows] == ["Руководитель бюро", "Секретарь"]


# --- Права и работа через API -------------------------------------------------


async def _participant_with_status(session, world, name, telegram_id, status):
    member = Member(region_id=world["moscow"].id, full_name=name, status=status)
    session.add(member)
    await session.flush()
    user = User(full_name=name, role=ROLE_PARTICIPANT, telegram_id=telegram_id, member_id=member.id)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def test_activist_does_not_see_the_list(client, world, session):
    """Список закрыт от активистов — так решило руководство."""
    activist = await _participant_with_status(
        session, world, "Активист Один", 4001, MEMBER_STATUS_ACTIVIST)
    login(activist)
    assert (await client.get("/api/bureau")).status_code == 403


@pytest.mark.parametrize("status", [MEMBER_STATUS_MEMBER, MEMBER_STATUS_ALUMNI])
async def test_members_and_alumni_see_the_list(client, world, session, status):
    """Выпускник когда-то был членом и остаётся своим — видит наравне."""
    person = await _participant_with_status(session, world, f"Человек {status}", 4002, status)
    login(person)
    assert (await client.get("/api/bureau")).status_code == 200


async def test_leader_sees_the_list_regardless_of_status(client, world):
    """Роль важнее статуса: иначе руководителя-активиста не пустило бы туда,
    куда пускают его подопечных, и это выглядело бы поломкой."""
    login(world["leader_moscow"])
    assert (await client.get("/api/bureau")).status_code == 200


async def test_only_federal_can_edit(client, world, session):
    """Править состав бюро может федеральный координатор, но не руководитель
    отделения."""
    member = Member(region_id=world["moscow"].id, full_name="Кандидат Один", status=MEMBER_STATUS_MEMBER)
    session.add(member)
    await session.flush()
    session.add(User(full_name="Кандидат Один", role=ROLE_PARTICIPANT,
                     telegram_id=4010, member_id=member.id))
    await session.commit()

    login(world["leader_moscow"])
    denied = await client.post("/api/bureau", json={"member_id": member.id, "title": "Куратор"})
    assert denied.status_code == 403

    login(world["federal"])
    added = await client.post("/api/bureau", json={"member_id": member.id, "title": "Куратор Сибири"})
    assert added.status_code == 200, added.text
    assert added.json()["title"] == "Куратор Сибири"


async def test_person_without_cabinet_cannot_be_added(client, world, session):
    """В бюро нужен аккаунт: без кабинета человек не получит ни задач, ни
    доступа, и запись о нём была бы пустой."""
    member = Member(region_id=world["moscow"].id, full_name="Без Кабинета", status=MEMBER_STATUS_MEMBER)
    session.add(member)
    await session.commit()

    login(world["federal"])
    response = await client.post("/api/bureau", json={"member_id": member.id, "title": "Куратор"})
    assert response.status_code == 400
    assert "нет личного кабинета" in response.json()["detail"]


async def test_bureau_title_shows_in_profile_for_everyone(client, world, session):
    """Строка в профиле видна всем — включая активиста, которому сам список
    закрыт. Так решило руководство: закрыт удобный список, а не факт."""
    person = await _participant_with_status(session, world, "Член Бюро", 4020, MEMBER_STATUS_MEMBER)
    session.add(BureauMember(user_id=person.id, title="Секретарь"))
    await session.commit()

    activist = await _participant_with_status(
        session, world, "Активист Два", 4021, MEMBER_STATUS_ACTIVIST)
    login(activist)

    profile = (await client.get(f"/api/profile/{person.id}")).json()
    assert profile["bureau_title"] == "Секретарь"
