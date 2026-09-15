"""Вузовская ячейка: руководитель ячейки видит и правит только свою ячейку
внутри региона, руководитель региона — по-прежнему весь регион целиком
(регресс тут был бы особенно незаметным — потому и отдельный файл)."""

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from database.models import (
    ROLE_PARTICIPANT,
    Event,
    Member,
    ShopPurchase,
    Transaction,
    UniversityCell,
    User,
)

from tests.conftest import login
from utils.tz import today as tz_today


async def _add_member(session, region_id, cell_id, name):
    member = Member(region_id=region_id, cell_id=cell_id, full_name=name)
    session.add(member)
    await session.commit()
    await session.refresh(member)
    return member


async def _add_sibling_member(session, world, name="Соседний Человек"):
    cell = UniversityCell(region_id=world["moscow"].id, name=f"Соседняя ячейка {name}")
    session.add(cell)
    await session.flush()
    return await _add_member(session, world["moscow"].id, cell.id, name)


async def test_cell_leader_sees_only_own_cell_members(client, session, world):
    region_id = world["moscow"].id
    cell_id = world["mgimo"].id
    await _add_member(session, region_id, cell_id, "Ячеечный Человек")
    await _add_member(session, region_id, None, "Региональный Человек")

    login(world["cell_leader"])
    listed = await client.get(f"/api/members?region_id={region_id}")
    assert listed.status_code == 200
    names = {item["full_name"] for item in listed.json()["items"]}
    assert names == {"Ячеечный Человек"}

    login(world["leader_moscow"])
    listed_all = await client.get(f"/api/members?region_id={region_id}")
    names_all = {item["full_name"] for item in listed_all.json()["items"]}
    assert names_all == {"Ячеечный Человек", "Региональный Человек"}


async def test_cell_leader_create_forces_own_cell(client, world):
    region_id = world["moscow"].id
    other_cell_id = 999  # несуществующая/чужая — сервер не должен ей доверять

    login(world["cell_leader"])
    created = await client.post(
        "/api/members",
        json={"region_id": region_id, "cell_id": other_cell_id, "full_name": "Проверка Владения"},
    )
    assert created.status_code == 200, created.text
    assert created.json()["cell_id"] == world["mgimo"].id


async def test_cell_leader_cannot_touch_foreign_cell_member(client, session, world):
    region_id = world["moscow"].id
    foreign = await _add_member(session, region_id, None, "Чужой Регионалец")

    login(world["cell_leader"])
    patched = await client.patch(f"/api/members/{foreign.id}", json={"phone": "+7 900 000-00-00"})
    assert patched.status_code == 403

    deleted = await client.delete(f"/api/members/{foreign.id}")
    assert deleted.status_code == 403


async def test_cell_leader_search_and_quest_read_stay_in_own_cell(client, session, world):
    own = await _add_member(session, world["moscow"].id, world["mgimo"].id, "Иван Проверочный")
    foreign = await _add_sibling_member(session, world, "Иван Соседний")

    login(world["cell_leader"])
    searched = await client.get("/api/members/search", params={"q": "Иван", "region_id": world["moscow"].id})
    assert searched.status_code == 200
    assert {item["id"] for item in searched.json()["items"]} == {own.id}
    assert (await client.get(f"/api/members/{own.id}/quests")).status_code == 200
    assert (await client.get(f"/api/members/{foreign.id}/quests")).status_code == 403

    login(world["leader_moscow"])
    searched = await client.get("/api/members/search", params={"q": "Иван", "region_id": world["moscow"].id})
    assert {item["id"] for item in searched.json()["items"]} == {own.id, foreign.id}
    assert (await client.get(f"/api/members/{foreign.id}/quests")).status_code == 200


async def test_cell_leader_cannot_read_or_acknowledge_foreign_purchases(client, session, world):
    own = await _add_member(session, world["moscow"].id, world["mgimo"].id, "Свой Покупатель")
    foreign = await _add_sibling_member(session, world, "Чужой Покупатель")
    own_purchase = ShopPurchase(member_id=own.id, item_id="sticker", kind="physical", price_stars=5)
    foreign_purchase = ShopPurchase(member_id=foreign.id, item_id="chevron", kind="physical", price_stars=10)
    session.add_all([own_purchase, foreign_purchase])
    await session.commit()

    login(world["cell_leader"])
    assert (await client.get(f"/api/members/{foreign.id}/shop-purchases")).status_code == 403
    await session.refresh(foreign_purchase)
    assert foreign_purchase.seen_by_leader is False

    assert (await client.get(f"/api/members/{own.id}/shop-purchases")).status_code == 200
    await session.refresh(own_purchase)
    assert own_purchase.seen_by_leader is True


async def test_read_only_coordinator_does_not_acknowledge_leader_purchase(client, session, world):
    member = await _add_member(session, world["moscow"].id, None, "Покупатель Координатора")
    purchase = ShopPurchase(member_id=member.id, item_id="sticker", kind="physical", price_stars=5)
    session.add(purchase)
    await session.commit()

    login(world["coordinator"])
    assert (await client.get(f"/api/members/{member.id}/shop-purchases")).status_code == 200
    await session.refresh(purchase)
    assert purchase.seen_by_leader is False


async def test_read_only_impersonation_does_not_acknowledge_purchase(client, session, world):
    member = await _add_member(session, world["moscow"].id, None, "Покупатель в режиме просмотра")
    purchase = ShopPurchase(member_id=member.id, item_id="sticker", kind="physical", price_stars=5)
    session.add(purchase)
    await session.commit()

    world["leader_moscow"]._impersonated_by = world["superuser"]
    login(world["leader_moscow"])
    assert (await client.get(f"/api/members/{member.id}/shop-purchases")).status_code == 200
    await session.refresh(purchase)
    assert purchase.seen_by_leader is False


@pytest.mark.parametrize("format", ["xlsx", "pdf"])
async def test_cell_leader_cannot_export_full_region_report(client, world, format):
    login(world["cell_leader"])
    response = await client.get(
        "/api/reports/region",
        params={"region_id": world["moscow"].id, "format": format},
    )
    assert response.status_code == 403


async def test_cell_leader_cannot_assign_foreign_cell_members_to_event_or_task(client, session, world):
    own = await _add_member(session, world["moscow"].id, world["mgimo"].id, "Свой Ответственный")
    foreign = await _add_sibling_member(session, world, "Чужой Ответственный")
    login(world["cell_leader"])

    rejected_event = await client.post(
        "/api/events",
        json={
            "region_id": world["moscow"].id,
            "title": "Закрытая встреча",
            "date": "2026-10-10",
            "description": "Описание",
            "responsible_member_id": foreign.id,
        },
    )
    assert rejected_event.status_code == 400

    created = await client.post(
        "/api/events",
        json={
            "region_id": world["moscow"].id,
            "title": "Своя встреча",
            "date": "2026-10-10",
            "description": "Описание",
            "responsible_member_id": own.id,
        },
    )
    assert created.status_code == 200, created.text
    event_id = created.json()["id"]
    rejected_event_patch = await client.patch(
        f"/api/events/{event_id}",
        json={"responsible_member_id": foreign.id},
    )
    assert rejected_event_patch.status_code == 400

    rejected_task = await client.post(
        f"/api/events/{event_id}/tasks",
        json={"title": "Чужая задача", "due_date": "2026-10-09", "assignee_member_id": foreign.id},
    )
    assert rejected_task.status_code == 400

    accepted_task = await client.post(
        f"/api/events/{event_id}/tasks",
        json={"title": "Своя задача", "due_date": "2026-10-09", "assignee_member_id": own.id},
    )
    assert accepted_task.status_code == 200, accepted_task.text
    rejected_task_patch = await client.patch(
        f"/api/events/{event_id}/tasks/{accepted_task.json()['id']}",
        json={"assignee_member_id": foreign.id},
    )
    assert rejected_task_patch.status_code == 400


async def test_cell_leader_events_and_finance_scoped(client, session, world):
    region_id = world["moscow"].id
    cell_id = world["mgimo"].id

    # От сегодня, а не календарным числом: проверяется список предстоящих, а
    # записанная числом «будущая» дата однажды становится прошлым, и тест
    # ломается сам по себе — так уже случилось с test_task_lifecycle.
    own_event = Event(region_id=region_id, cell_id=cell_id, title="Ячеечная встреча",
                      date=tz_today() + timedelta(days=17))
    regional_event = Event(region_id=region_id, cell_id=None, title="Региональный форум",
                           date=tz_today() + timedelta(days=19))
    session.add_all([own_event, regional_event])
    await session.commit()

    session.add_all(
        [
            Transaction(region_id=region_id, cell_id=cell_id, amount=50_00, type="expense", date=date(2026, 9, 1)),
            Transaction(region_id=region_id, cell_id=None, amount=999_00, type="expense", date=date(2026, 9, 1)),
        ]
    )
    await session.commit()

    login(world["cell_leader"])
    events = await client.get(f"/api/events?region_id={region_id}&scope=all")
    assert [e["title"] for e in events.json()["items"]] == ["Ячеечная встреча"]

    overview = await client.get(f"/api/finance/overview?region_id={region_id}&period=all")
    assert overview.json()["expense"] == 50_00  # не 999_00 + 50_00 — только своя ячейка


async def test_cell_leader_dashboard_scoped(client, session, world):
    """Регресс на находку из ручной проверки: главный экран изначально не был
    научен сужению по ячейке и показывал баланс/мероприятия всего региона."""
    region_id = world["moscow"].id
    cell_id = world["mgimo"].id

    # От сегодня, а не календарным числом: проверяется список предстоящих, а
    # записанная числом «будущая» дата однажды становится прошлым, и тест
    # ломается сам по себе — так уже случилось с test_task_lifecycle.
    own_event = Event(region_id=region_id, cell_id=cell_id, title="Ячеечная встреча",
                      date=tz_today() + timedelta(days=17))
    regional_event = Event(region_id=region_id, cell_id=None, title="Региональный форум",
                           date=tz_today() + timedelta(days=19))
    session.add_all([own_event, regional_event])
    session.add_all(
        [
            Transaction(region_id=region_id, cell_id=cell_id, amount=50_00, type="expense", date=date(2026, 9, 1)),
            Transaction(region_id=region_id, cell_id=None, amount=999_00, type="expense", date=date(2026, 9, 1)),
        ]
    )
    await session.commit()

    login(world["cell_leader"])
    dashboard = await client.get(f"/api/dashboard?region_id={region_id}")
    data = dashboard.json()
    assert [e["title"] for e in data["upcoming_events"]] == ["Ячеечная встреча"]
    assert data["finance"]["balance"] == -50_00  # не -(999_00 + 50_00) — только своя ячейка


async def test_cell_leader_correspondents_and_region_leader_sees_cell_down(session, world):
    from utils.access import correspondents

    cell_people = await correspondents(session, world["cell_leader"])
    assert [p.full_name for p in cell_people] == [world["leader_moscow"].full_name]

    leader_people = await correspondents(session, world["leader_moscow"])
    assert world["cell_leader"].full_name in {p.full_name for p in leader_people}


async def test_cell_vk_url_accepts_only_https_vk(client, world):
    login(world["leader_moscow"])

    spoofed = await client.patch(
        f"/api/cells/{world['mgimo'].id}",
        json={"vk_url": "https://vk.com.evil.example/community"},
    )
    assert spoofed.status_code == 422

    insecure = await client.patch(
        f"/api/cells/{world['mgimo'].id}",
        json={"vk_url": "http://vk.com/community"},
    )
    assert insecure.status_code == 422

    valid = await client.patch(
        f"/api/cells/{world['mgimo'].id}",
        json={"vk_url": "https://vk.com/community"},
    )
    assert valid.status_code == 200
    assert valid.json()["vk_url"] == "https://vk.com/community"


# --- Назначение руководителя ячейки (POST /cells/{id}/leader) --------------
# Перенесено из бывших кнопок бота (handlers/create_account.py, удалён) в
# саму вкладку «Вузовские ячейки» — руководитель региона назначает прямо
# из карточки ячейки, план «Снизу вверх», §4.


@pytest.fixture
async def moscow_member(session, world):
    """Уже саморегистрирован (services/admin_actions.py::_resolve_or_promote
    назначает роль только такому человеку — telegram_id уже известен)."""
    member = Member(
        region_id=world["moscow"].id,
        cell_id=world["mgimo"].id,
        full_name="Волков Михаил",
    )
    session.add(member)
    await session.flush()
    session.add(User(full_name=member.full_name, role=ROLE_PARTICIPANT, member_id=member.id, telegram_id=930001))
    await session.commit()
    await session.refresh(member)
    return member


async def test_region_leader_can_assign_cell_leader(client, session, world, moscow_member):
    login(world["leader_moscow"])
    response = await client.post(f"/api/cells/{world['mgimo'].id}/leader", json={"member_id": moscow_member.id})
    assert response.status_code == 200, response.text

    await session.refresh(world["mgimo"])
    user = (await session.execute(select(User).where(User.member_id == moscow_member.id))).scalar_one()
    assert world["mgimo"].leader_user_id == user.id


async def test_region_leader_cannot_assign_cell_leader_from_sibling_cell(client, session, world):
    candidate = await _add_sibling_member(session, world, "Кандидат из соседней ячейки")
    account = User(
        full_name=candidate.full_name,
        role=ROLE_PARTICIPANT,
        member_id=candidate.id,
        telegram_id=930099,
    )
    session.add(account)
    await session.commit()

    login(world["leader_moscow"])
    response = await client.post(f"/api/cells/{world['mgimo'].id}/leader", json={"member_id": candidate.id})
    assert response.status_code == 400
    await session.refresh(account)
    assert account.role == ROLE_PARTICIPANT


async def test_cell_leader_cannot_assign_another_cell_leader(client, world, moscow_member):
    """Руководитель самой ячейки не назначает себе замену — это в компетенции
    руководителя региона (см. api/routers/cells.py::assign_cell_leader)."""
    login(world["cell_leader"])
    response = await client.post(f"/api/cells/{world['mgimo'].id}/leader", json={"member_id": moscow_member.id})
    assert response.status_code == 403


async def test_leader_cannot_assign_cell_leader_in_other_region(client, world):
    """Ячейка МГИМО принадлежит Москве — руководитель Тулы её не видит. Проверка
    региона идёт раньше проверки member_id, поэтому даже несуществующий id тут не важен."""
    login(world["leader_tula"])
    response = await client.post(f"/api/cells/{world['mgimo'].id}/leader", json={"member_id": 999999})
    assert response.status_code == 403


async def test_coordinator_and_federal_cannot_assign_cell_leader(client, world, moscow_member):
    login(world["coordinator"])
    coordinator_response = await client.post(
        f"/api/cells/{world['mgimo'].id}/leader", json={"member_id": moscow_member.id}
    )
    assert coordinator_response.status_code == 403

    login(world["federal"])
    federal_response = await client.post(f"/api/cells/{world['mgimo'].id}/leader", json={"member_id": moscow_member.id})
    assert federal_response.status_code == 403
