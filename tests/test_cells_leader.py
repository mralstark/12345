"""Должность руководителя вузовской ячейки.

Раньше её можно было только сменить. Если человек выпустился, а поставить
на его место некого, ячейка оставалась с бывшим во главе навсегда — снять
было нечем.
"""

from database.models import (
    MEMBER_STATUS_ACTIVIST,
    ROLE_CELL_LEADER,
    ROLE_PARTICIPANT,
    Member,
    University,
    UniversityCell,
    User,
)
from sqlalchemy import select

from tests.conftest import login


async def _cell_with_people(session, region_id):
    university = University(name="СФУ")
    session.add(university)
    await session.flush()
    cell = UniversityCell(region_id=region_id, university_id=university.id, name="СФУ")
    session.add(cell)
    await session.flush()

    # Один с аккаунтом — его можно назначить; второй без — нельзя.
    registered = Member(region_id=region_id, full_name="Первый Первов",
                        status=MEMBER_STATUS_ACTIVIST, cell_id=cell.id)
    stranger = Member(region_id=region_id, full_name="Второй Второв",
                      status=MEMBER_STATUS_ACTIVIST, cell_id=cell.id)
    session.add_all([registered, stranger])
    await session.flush()
    session.add(User(full_name=registered.full_name, role=ROLE_PARTICIPANT,
                     member_id=registered.id, telegram_id=7101))
    await session.commit()
    await session.refresh(cell)
    return cell, registered, stranger


async def test_cell_members_say_who_can_be_appointed(client, world, session):
    """Список должен сам говорить, кого назначить нельзя: назначение — это
    повышение существующего аккаунта, и человек без саморегистрации его не
    имеет. Раньше отказ приходил уже после нажатия."""
    cell, registered, stranger = await _cell_with_people(session, world["moscow"].id)
    login(world["leader_moscow"])

    members = (await client.get(f"/api/cells/{cell.id}")).json()["members"]
    by_name = {m["full_name"]: m for m in members}

    assert by_name["Первый Первов"]["has_account"] is True
    assert by_name["Второй Второв"]["has_account"] is False


async def test_leader_can_be_removed(client, world, session):
    cell, registered, _ = await _cell_with_people(session, world["moscow"].id)
    login(world["leader_moscow"])

    assert (await client.post(
        f"/api/cells/{cell.id}/leader", json={"member_id": registered.id})).status_code == 200
    assert (await client.get(f"/api/cells/{cell.id}")).json()["leader_name"] == "Первый Первов"

    response = await client.delete(f"/api/cells/{cell.id}/leader")

    assert response.status_code == 200
    assert response.json()["removed"] == "Первый Первов"
    assert (await client.get(f"/api/cells/{cell.id}")).json()["leader_name"] is None


async def test_removed_leader_keeps_the_account(client, world, session):
    """Снятие — про должность, а не про человека: кабинет у него остаётся."""
    cell, registered, _ = await _cell_with_people(session, world["moscow"].id)
    login(world["leader_moscow"])
    await client.post(f"/api/cells/{cell.id}/leader", json={"member_id": registered.id})

    await client.delete(f"/api/cells/{cell.id}/leader")

    person = (await session.execute(
        User.__table__.select().where(User.member_id == registered.id))).first()
    assert person is not None, "человека удалять не должны"
    assert person.role != ROLE_CELL_LEADER, "должность снята — роль тоже"


async def test_removing_twice_is_harmless(client, world, session):
    """Второе нажатие на «Снять» ничего не ломает: снимать уже нечего."""
    cell, _, _ = await _cell_with_people(session, world["moscow"].id)
    login(world["leader_moscow"])

    response = await client.delete(f"/api/cells/{cell.id}/leader")

    assert response.status_code == 200
    assert response.json()["removed"] is None


async def test_stranger_cannot_remove(client, world, session):
    """Должностью в ячейке распоряжается руководитель её отделения."""
    cell, _, _ = await _cell_with_people(session, world["moscow"].id)
    login(world["leader_tula"])

    assert (await client.delete(f"/api/cells/{cell.id}/leader")).status_code == 403


# --- Пустые ячейки ------------------------------------------------------------


async def test_empty_cell_is_not_listed(client, world, session):
    """Ячейка заводится сама от вуза, вписанного человеку, и остаётся, если вуз
    потом исправили. В Барнауле так осталась «АСК СПБ» — заведена опечаткой и с
    тех пор пустая. Списку она не нужна."""
    from database.models import MEMBER_STATUS_ACTIVIST

    university = University(name="АСК СПБ")
    session.add(university)
    await session.flush()
    empty = UniversityCell(region_id=world["moscow"].id, university_id=university.id, name="АСК СПБ")
    session.add(empty)
    populated, _, _ = await _cell_with_people(session, world["moscow"].id)
    await session.commit()

    login(world["leader_moscow"])
    names = [c["name"] for c in (await client.get(
        f"/api/cells?region_id={world['moscow'].id}")).json()["items"]]

    assert "СФУ" in names
    assert "АСК СПБ" not in names


async def test_hidden_cell_comes_back_with_its_first_member(client, world, session):
    """Прятать безопасно: ячейка не удалена, и стоит кому-то снова назвать этот
    вуз — resolve_member_cell найдёт ту же самую, со всей её историей."""
    from database.models import MEMBER_STATUS_ACTIVIST
    from utils.university_cells import resolve_member_cell

    university = University(name="АСК СПБ")
    session.add(university)
    await session.flush()
    empty = UniversityCell(region_id=world["moscow"].id, university_id=university.id, name="АСК СПБ")
    session.add(empty)
    await session.commit()
    await session.refresh(empty)

    found = await resolve_member_cell(session, world["moscow"].id, university.id)
    assert found.id == empty.id, "завелась вторая ячейка вместо возврата прежней"

    session.add(Member(region_id=world["moscow"].id, full_name="Новый Человек",
                       status=MEMBER_STATUS_ACTIVIST, cell_id=empty.id))
    await session.commit()

    login(world["leader_moscow"])
    names = [c["name"] for c in (await client.get(
        f"/api/cells?region_id={world['moscow'].id}")).json()["items"]]
    assert "АСК СПБ" in names


async def test_cell_leader_cabinet_is_named_by_university(client, session, world):
    """Кабинет руководителя ячейки подписан вузом, а не регионом.

    Регион ему виден только как рамка вокруг ячейки — состава всего города там
    нет. Надпись «Санкт-Петербург» на переключателе обещала именно его.
    """
    cell, registered, _ = await _cell_with_people(session, world["moscow"].id)
    login(world["leader_moscow"])
    assert (await client.post(
        f"/api/cells/{cell.id}/leader", json={"member_id": registered.id}
    )).status_code == 200

    leader = (await session.execute(
        select(User).where(User.member_id == registered.id)
    )).scalar_one()
    login(leader)
    me = (await client.get("/api/me")).json()
    assert me["cell"] is not None
    assert me["cell"]["name"] == "СФУ"
    assert me["cell"]["region_id"] == world["moscow"].id


async def test_region_leader_has_no_cell(client, world):
    """У руководителя отделения ячейки нет — кабинет остаётся регионом."""
    login(world["leader_moscow"])
    assert (await client.get("/api/me")).json()["cell"] is None
