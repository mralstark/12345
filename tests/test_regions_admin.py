"""Вкладка «Регионы» в Mini App (api/routers/regions.py) — создание региона,
назначение руководителя/координатора. Замена бывших кнопок бота
(handlers/create_account.py, удалён при переходе на самостоятельную
регистрацию, план «Снизу вверх», §4). Назначение руководителя вузовской
ячейки — отдельно, см. tests/test_cells.py (api/routers/cells.py)."""

from datetime import date

import pytest
from sqlalchemy import select

from database.models import (
    ROLE_PARTICIPANT,
    Category,
    CoordinatorRegion,
    Document,
    Event,
    Member,
    MembershipApplication,
    Region,
    Transaction,
    University,
    UniversityCell,
    User,
)
from tests.conftest import login


async def _self_registered_member(session, region_id, full_name, telegram_id):
    """Уже саморегистрирован (services/admin_actions.py::_resolve_or_promote
    назначает роль только такому человеку — telegram_id уже известен)."""
    member = Member(region_id=region_id, full_name=full_name)
    session.add(member)
    await session.flush()
    session.add(User(full_name=full_name, role=ROLE_PARTICIPANT, member_id=member.id, telegram_id=telegram_id))
    await session.commit()
    await session.refresh(member)
    return member


@pytest.fixture
async def moscow_member(session, world):
    return await _self_registered_member(session, world["moscow"].id, "Волков Михаил", 930002)


@pytest.fixture
async def tula_member(session, world):
    return await _self_registered_member(session, world["tula"].id, "Орлов Олег", 930003)


async def test_federal_can_create_region(client, world):
    login(world["federal"])
    response = await client.post("/api/regions", json={"name": "Казань"})
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Казань"


async def test_coordinator_cannot_create_region(client, world):
    login(world["coordinator"])
    response = await client.post("/api/regions", json={"name": "Тверь"})
    assert response.status_code == 403


async def test_leader_cannot_see_regions_tab(client, world):
    login(world["leader_moscow"])
    response = await client.get("/api/regions")
    assert response.status_code == 403


async def test_regions_list_shows_context_and_permission_flags(client, world):
    login(world["coordinator"])
    response = await client.get("/api/regions")
    data = response.json()
    by_name = {item["name"]: item for item in data["items"]}
    assert by_name["Москва"]["leader_name"] == world["leader_moscow"].full_name
    assert by_name["Москва"]["can_assign_leader"] is True
    assert by_name["Тула"]["can_assign_leader"] is False
    assert data["can_create_region"] is False
    assert data["can_assign_coordinator"] is False


async def test_federal_can_assign_leader_in_any_region(client, session, world, tula_member):
    login(world["federal"])
    response = await client.post(f"/api/regions/{world['tula'].id}/leader", json={"member_id": tula_member.id})
    assert response.status_code == 200, response.text

    await session.refresh(world["tula"])
    user = (await session.execute(select(User).where(User.member_id == tula_member.id))).scalar_one()
    assert world["tula"].leader_user_id == user.id


async def test_federal_cannot_assign_region_leader_from_another_region(client, session, world, tula_member):
    login(world["federal"])
    response = await client.post(
        f"/api/regions/{world['moscow'].id}/leader",
        json={"member_id": tula_member.id},
    )
    assert response.status_code == 400
    account = (await session.execute(select(User).where(User.member_id == tula_member.id))).scalar_one()
    await session.refresh(account)
    assert account.role == ROLE_PARTICIPANT


async def test_management_role_cannot_be_silently_overwritten(client, session, world):
    member = Member(region_id=world["moscow"].id, full_name="Действующий Координатор")
    session.add(member)
    await session.flush()
    account = User(
        full_name=member.full_name,
        role="coordinator",
        member_id=member.id,
        telegram_id=930098,
    )
    session.add(account)
    await session.flush()
    link = CoordinatorRegion(coordinator_user_id=account.id, region_id=world["tula"].id)
    session.add(link)
    await session.commit()

    login(world["federal"])
    response = await client.post(
        f"/api/regions/{world['moscow'].id}/leader",
        json={"member_id": member.id},
    )
    assert response.status_code == 400
    await session.refresh(account)
    assert account.role == "coordinator"
    assert await session.get(CoordinatorRegion, link.id) is not None


async def test_coordinator_can_assign_leader_only_in_own_region(client, world, moscow_member, tula_member):
    login(world["coordinator"])  # world: координатор назначен только на Москву
    ok = await client.post(f"/api/regions/{world['moscow'].id}/leader", json={"member_id": moscow_member.id})
    assert ok.status_code == 200, ok.text

    forbidden = await client.post(f"/api/regions/{world['tula'].id}/leader", json={"member_id": tula_member.id})
    assert forbidden.status_code == 403


async def test_leader_cannot_assign_region_leader(client, world, moscow_member):
    """Назначение руководителя региона — не в компетенции самого руководителя
    (в отличие от назначения руководителя своей вузовской ячейки — там своя
    проверка, см. tests/test_cells.py)."""
    login(world["leader_moscow"])
    response = await client.post(f"/api/regions/{world['moscow'].id}/leader", json={"member_id": moscow_member.id})
    assert response.status_code == 403


async def test_federal_can_assign_coordinator(client, session, world, moscow_member):
    login(world["federal"])
    response = await client.post(
        "/api/regions/coordinators", json={"member_id": moscow_member.id, "region_ids": [world["tula"].id]}
    )
    assert response.status_code == 200, response.text
    user_id = response.json()["id"]
    links = (
        await session.execute(select(CoordinatorRegion).where(CoordinatorRegion.coordinator_user_id == user_id))
    ).scalars().all()
    assert {link.region_id for link in links} == {world["tula"].id}


async def test_coordinator_cannot_assign_coordinator(client, world, moscow_member):
    login(world["coordinator"])
    response = await client.post(
        "/api/regions/coordinators", json={"member_id": moscow_member.id, "region_ids": [world["moscow"].id]}
    )
    assert response.status_code == 403


async def test_federal_can_rename_region(client, session, world):
    login(world["federal"])
    response = await client.patch(f"/api/regions/{world['tula'].id}", json={"name": "Тула Новая"})
    assert response.status_code == 200, response.text
    await session.refresh(world["tula"])
    assert world["tula"].name == "Тула Новая"


async def test_coordinator_cannot_rename_region(client, world):
    login(world["coordinator"])
    response = await client.patch(f"/api/regions/{world['tula'].id}", json={"name": "Тула Ещё"})
    assert response.status_code == 403


async def test_coordinator_cannot_delete_region(client, world):
    login(world["coordinator"])
    response = await client.delete(f"/api/regions/{world['tula'].id}")
    assert response.status_code == 403


async def test_federal_archives_region_without_destroying_its_data(client, session, world):
    """Опасное действие в UI закрывает доступ, но оставляет данные для восстановления."""
    region = Region(name="Регион на удаление", application_code="DELXXXXXXXXX")
    session.add(region)
    await session.flush()

    university = University(name="Вуз региона на удаление", region_id=region.id)
    session.add(university)
    await session.flush()

    cell = UniversityCell(region_id=region.id, name="Ячейка на удаление", university_id=university.id)
    session.add(cell)
    await session.flush()

    member = Member(region_id=region.id, cell_id=cell.id, full_name="Иванов Иван", university_id=university.id)
    session.add(member)
    await session.flush()

    member_user = User(full_name=member.full_name, role="participant", member_id=member.id, telegram_id=777444)
    session.add(member_user)
    await session.flush()

    category = Category(region_id=region.id, name="Прочее", type="expense")
    session.add(category)
    await session.flush()

    event = Event(region_id=region.id, cell_id=cell.id, title="Событие региона", date=date(2026, 1, 1))
    transaction = Transaction(region_id=region.id, category_id=category.id, amount=100, type="expense", date=date(2026, 1, 1))
    document = Document(region_id=region.id, title="Файл", stored_path="x", original_name="x.txt")
    application = MembershipApplication(region_id=region.id, telegram_id=999888, full_name="Кто-то ещё")
    coord_link = CoordinatorRegion(coordinator_user_id=world["coordinator"].id, region_id=region.id)
    session.add_all([event, transaction, document, application, coord_link])
    await session.commit()

    region_id, university_id, member_id, member_user_id = region.id, university.id, member.id, member_user.id

    login(world["federal"])
    response = await client.delete(f"/api/regions/{region_id}")
    assert response.status_code == 200, response.text

    session.expire_all()
    archived = await session.get(Region, region_id)
    assert archived is not None
    assert archived.is_active is False
    assert archived.application_code is None
    assert await session.get(Member, member_id) is not None
    assert await session.get(User, member_user_id) is not None

    # Все связанные записи остаются на месте.
    university_after = await session.get(University, university_id)
    assert university_after is not None
    assert university_after.region_id == region_id


# --- Снятие с должности -------------------------------------------------------
# Раньше должность можно было только сменить: убрать человека, не назначая
# нового, было нечем, и регион нельзя было оставить без руководителя.


async def test_leader_can_be_removed_without_replacement(client, session, world, tula_member):
    login(world["federal"])
    await client.post(f"/api/regions/{world['tula'].id}/leader", json={"member_id": tula_member.id})
    person = (await session.execute(select(User).where(User.member_id == tula_member.id))).scalar_one()

    removed = await client.delete(f"/api/regions/{world['tula'].id}/leader")
    assert removed.status_code == 200, removed.text

    await session.refresh(world["tula"])
    await session.refresh(person)
    assert world["tula"].leader_user_id is None
    # Роль снимается вместе с должностью: руководитель без региона — это не
    # почётное звание, а пустой управленческий кабинет.
    assert person.role == ROLE_PARTICIPANT


async def test_removing_leader_twice_says_there_is_none(client, world):
    """Повторное снятие — не молчаливое «ок», а внятный отказ: иначе человек
    не поймёт, снял он кого-то или нажал впустую."""
    login(world["federal"])
    assert (await client.delete(f"/api/regions/{world['tula'].id}/leader")).status_code == 200
    second = await client.delete(f"/api/regions/{world['tula'].id}/leader")
    assert second.status_code == 400
    assert "нет руководителя" in second.json()["detail"]


async def test_replacing_leader_demotes_the_previous_one(client, session, world, moscow_member):
    """Смена руководителя оставляла прежнего с ролью, но без региона — его
    кабинет показывал бы пустоту."""
    previous = world["leader_moscow"]
    assert previous.role == "leader"

    login(world["federal"])
    ok = await client.post(f"/api/regions/{world['moscow'].id}/leader", json={"member_id": moscow_member.id})
    assert ok.status_code == 200, ok.text

    await session.refresh(previous)
    assert previous.role == ROLE_PARTICIPANT


async def test_coordinator_can_be_removed(client, session, world):
    coordinator = world["coordinator"]
    login(world["federal"])

    removed = await client.delete(f"/api/regions/coordinators/{coordinator.id}")
    assert removed.status_code == 200, removed.text

    await session.refresh(coordinator)
    assert coordinator.role == ROLE_PARTICIPANT
    left = (await session.execute(
        select(CoordinatorRegion).where(CoordinatorRegion.coordinator_user_id == coordinator.id)
    )).scalars().all()
    assert left == []


async def test_leader_cannot_remove_anyone(client, world):
    login(world["leader_moscow"])
    assert (await client.delete(f"/api/regions/{world['moscow'].id}/leader")).status_code == 403
    assert (await client.delete(f"/api/regions/coordinators/{world['coordinator'].id}")).status_code == 403
