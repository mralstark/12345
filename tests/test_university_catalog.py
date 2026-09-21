"""Каталог вузов и авто-создание ячеек по вузу (utils/university_cells.py,
services/admin_actions.py) — новая логика взамен свободного текста и
ручного выбора ячейки в форме «Состав»."""

import pytest
from sqlalchemy import select

from database.models import CoordinatorRegion, Member, ROLE_PARTICIPANT, University, UniversityCell, User
from services.admin_actions import create_coordinator, create_federal, create_region
from tests.conftest import login
from utils.university_cells import resolve_member_cell


async def test_resolve_member_cell_creates_on_first_use(session, world):
    university = University(name="Тестовый Технический Университет")
    session.add(university)
    await session.flush()

    cell = await resolve_member_cell(session, world["moscow"].id, university.id)
    await session.commit()

    assert cell is not None
    assert cell.region_id == world["moscow"].id
    assert cell.university_id == university.id
    assert cell.name == university.name


async def test_resolve_member_cell_is_idempotent(session, world):
    university = University(name="Ещё Один Университет")
    session.add(university)
    await session.flush()

    first = await resolve_member_cell(session, world["moscow"].id, university.id)
    await session.commit()
    second = await resolve_member_cell(session, world["moscow"].id, university.id)
    await session.commit()

    assert first.id == second.id
    rows = (
        await session.execute(select(UniversityCell).where(UniversityCell.university_id == university.id))
    ).scalars().all()
    assert len(rows) == 1


async def test_resolve_member_cell_links_legacy_cell_instead_of_duplicating(session, world):
    """Ячейка, заведённая вручную до появления каталога (university_id=None),
    не должна задваиваться при первом упоминании соответствующего вуза —
    её нужно связать с каталогом, а не создавать вторую с тем же именем."""
    legacy_cell = UniversityCell(region_id=world["moscow"].id, name="Legacy University")
    session.add(legacy_cell)
    await session.flush()

    university = University(name="legacy university")  # другой регистр — должен всё равно совпасть
    session.add(university)
    await session.flush()

    resolved = await resolve_member_cell(session, world["moscow"].id, university.id)
    await session.commit()

    assert resolved.id == legacy_cell.id
    assert resolved.university_id == university.id
    total = (
        await session.execute(select(UniversityCell).where(UniversityCell.region_id == world["moscow"].id))
    ).scalars().all()
    assert len([c for c in total if c.name.lower() == "legacy university"]) == 1


async def test_resolve_member_cell_none_university_returns_none(session, world):
    assert await resolve_member_cell(session, world["moscow"].id, None) is None


async def test_create_member_auto_creates_cell_and_assigns_it(client, session, world):
    university = University(name="Университет Для API-теста", region_id=world["moscow"].id)
    session.add(university)
    await session.commit()
    await session.refresh(university)

    login(world["leader_moscow"])
    response = await client.post(
        "/api/members",
        json={
            "region_id": world["moscow"].id,
            "full_name": "Проверочный Студент",
            "status": "activist",
            "university_id": university.id,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["university_id"] == university.id
    assert body["cell_id"] is not None

    cell = await session.get(UniversityCell, body["cell_id"])
    assert cell.university_id == university.id
    assert cell.region_id == world["moscow"].id


async def test_cell_leader_cannot_set_foreign_university(client, session, world):
    """Руководитель ячейки не должен мочь через payload подсунуть человеку
    чужой вуз/ячейку — сервер всегда форсирует его собственную."""
    other_university = University(name="Совсем Другой Университет")
    session.add(other_university)
    await session.commit()
    await session.refresh(other_university)

    login(world["cell_leader"])
    response = await client.post(
        "/api/members",
        json={
            "region_id": world["moscow"].id,
            "full_name": "Подставной Студент",
            "status": "activist",
            "university_id": other_university.id,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["cell_id"] == world["mgimo"].id
    assert body["university_id"] == world["mgimo"].university_id


async def test_region_leadership_can_manage_university_catalog(client, session, world):
    login(world["leader_moscow"])
    created = await client.post(
        "/api/universities",
        json={"name": "Московский тестовый университет", "region_id": world["moscow"].id},
    )
    assert created.status_code == 200
    university_id = created.json()["id"]

    login(world["federal"])
    federal_created = await client.post(
        "/api/universities",
        json={"name": "Тульский федеральный тестовый вуз", "region_id": world["tula"].id},
    )
    assert federal_created.status_code == 200

    login(world["superuser"])
    listing = await client.get(f"/api/universities/manage?region_id={world['moscow'].id}")
    assert listing.status_code == 200
    assert listing.json()["items"][0]["id"] == university_id


async def test_university_catalog_rejects_roles_without_management_rights(client, world):
    payload = {"name": "Недоступный вуз", "region_id": world["moscow"].id}
    for actor in (world["coordinator"], world["cell_leader"]):
        login(actor)
        assert (await client.post("/api/universities", json=payload)).status_code == 403
        assert (
            await client.get(f"/api/universities/manage?region_id={world['moscow'].id}")
        ).status_code == 403


async def test_university_can_be_renamed_archived_and_restored(client, session, world):
    university = University(name="Старое название", region_id=world["moscow"].id)
    session.add(university)
    await session.commit()
    await session.refresh(university)

    login(world["leader_moscow"])
    member_response = await client.post(
        "/api/members",
        json={
            "region_id": world["moscow"].id,
            "full_name": "Участник Каталога",
            "university_id": university.id,
        },
    )
    assert member_response.status_code == 200

    renamed = await client.patch(
        f"/api/universities/{university.id}", json={"name": "Новое название"}
    )
    assert renamed.status_code == 200
    cell = await session.get(UniversityCell, member_response.json()["cell_id"])
    await session.refresh(cell)
    assert cell.name == "Новое название"

    archived = await client.patch(
        f"/api/universities/{university.id}", json={"is_active": False}
    )
    assert archived.status_code == 200
    search = await client.get(f"/api/universities?region_id={world['moscow'].id}&q=Новое")
    assert search.json()["items"] == []
    listing = await client.get(f"/api/universities/manage?region_id={world['moscow'].id}")
    assert listing.json()["items"][0]["members_total"] == 1
    assert listing.json()["items"][0]["is_active"] is False

    restored = await client.patch(
        f"/api/universities/{university.id}", json={"is_active": True}
    )
    assert restored.status_code == 200
    search = await client.get(f"/api/universities?region_id={world['moscow'].id}&q=Новое")
    assert search.json()["items"][0]["id"] == university.id


async def test_member_rejects_archived_or_foreign_university(client, session, world):
    archived = University(name="Архивный вуз", region_id=world["moscow"].id, is_active=False)
    foreign = University(name="Чужой вуз", region_id=world["tula"].id)
    session.add_all([archived, foreign])
    await session.commit()
    await session.refresh(archived)
    await session.refresh(foreign)
    login(world["leader_moscow"])

    for university in (archived, foreign):
        response = await client.post(
            "/api/members",
            json={
                "region_id": world["moscow"].id,
                "full_name": "Неверная привязка",
                "university_id": university.id,
            },
        )
        assert response.status_code == 400


async def _self_register(session, region_id, full_name, telegram_id):
    """То, что реально оставляет после себя саморегистрация
    (services/applications.py::approve_application) — участник с аккаунтом,
    ещё без управленческой роли."""
    member = Member(region_id=region_id, full_name=full_name)
    session.add(member)
    await session.flush()
    user = User(full_name=full_name, role=ROLE_PARTICIPANT, member_id=member.id, telegram_id=telegram_id)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def test_create_region_rejects_duplicate_name(session, world):
    with pytest.raises(ValueError):
        await create_region(session, "Москва")


async def test_create_federal_allows_several_at_once(session, world):
    """Несколько федеральных одновременно — доверенные люди-администраторы
    вместо одного технического superuser (план «Убираем технического superuser»)."""
    candidate2 = await _self_register(session, world["moscow"].id, "Второй Федеральный", 920002)
    candidate3 = await _self_register(session, world["moscow"].id, "Третий Федеральный", 920003)

    second = await create_federal(session, member_id=candidate2.member_id)
    assert second.role == "federal"
    third = await create_federal(session, member_id=candidate3.member_id)
    assert third.id != second.id


async def test_create_coordinator_creates_region_links(session, world):
    region_a = await create_region(session, "Регион А для координатора")
    region_b = await create_region(session, "Регион Б для координатора")
    candidate = await _self_register(session, region_a.id, "Новый Координатор", 920004)

    user = await create_coordinator(session, [region_a.id, region_b.id], member_id=candidate.member_id)

    links = (
        await session.execute(select(CoordinatorRegion).where(CoordinatorRegion.coordinator_user_id == user.id))
    ).scalars().all()
    assert {link.region_id for link in links} == {region_a.id, region_b.id}
