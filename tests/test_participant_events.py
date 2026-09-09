"""Календарь мероприятий глазами обычного участника (role=participant):
видит только мероприятия своего региона без бюджета, отмечается «иду/не иду»
через ту же EventAttendance, что и чек-лист руководителя (api/routers/events.py
::list_my_events, set_rsvp)."""

import pytest

from database.models import EVENT_STATUS_CANCELLED, ROLE_PARTICIPANT, Member, User
from tests.conftest import login


@pytest.fixture
async def participant(session, world):
    """Саморегистрированный участник — то, что оставляет после себя
    services/applications.py::approve_application (telegram_id уже есть)."""
    member = Member(region_id=world["moscow"].id, full_name="Волков Михаил")
    session.add(member)
    await session.flush()
    user = User(full_name=member.full_name, role=ROLE_PARTICIPANT, member_id=member.id, telegram_id=910001)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def _make_event(client, region_id, title="Собрание отделения", date="2026-09-01"):
    created = await client.post(
        "/api/events", json={"region_id": region_id, "title": title, "date": date, "description": "Описание"}
    )
    assert created.status_code == 200, created.text
    return created.json()


async def test_participant_sees_only_own_region_without_budget(client, world, participant):
    login(world["leader_moscow"])
    moscow_event = await _make_event(client, world["moscow"].id, title="Московское собрание")
    await client.patch(f"/api/events/{moscow_event['id']}", json={"planned_budget": 500000})

    login(world["leader_tula"])
    await _make_event(client, world["tula"].id, title="Тульское собрание")

    login(participant)
    resp = await client.get("/api/events/mine?scope=all")
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]

    assert [i["title"] for i in items] == ["Московское собрание"]
    item = items[0]
    assert "planned_budget" not in item
    assert "fact_expense" not in item
    assert "responsible_member_id" not in item
    assert item["going"] is None


async def test_participant_rsvp_reflected_for_organizer(client, world, participant):
    login(world["leader_moscow"])
    event = await _make_event(client, world["moscow"].id)

    login(participant)
    resp = await client.put(f"/api/events/{event['id']}/rsvp", json={"going": True})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True, "going": True}

    mine = (await client.get("/api/events/mine?scope=all")).json()["items"]
    assert mine[0]["going"] is True

    login(world["leader_moscow"])
    card = (await client.get(f"/api/events/{event['id']}")).json()
    assert participant.member_id in card["attended_member_ids"]

    login(participant)
    cleared = await client.put(f"/api/events/{event['id']}/rsvp", json={"going": None})
    assert cleared.status_code == 200
    mine_after = (await client.get("/api/events/mine?scope=all")).json()["items"]
    assert mine_after[0]["going"] is None


async def test_participant_cannot_rsvp_other_region(client, world, participant):
    login(world["leader_tula"])
    event = await _make_event(client, world["tula"].id)

    login(participant)
    resp = await client.put(f"/api/events/{event['id']}/rsvp", json={"going": True})
    assert resp.status_code == 403


async def test_participant_cannot_rsvp_non_planned_event(client, world, participant):
    login(world["leader_moscow"])
    event = await _make_event(client, world["moscow"].id)
    await client.patch(f"/api/events/{event['id']}", json={"status": EVENT_STATUS_CANCELLED})

    login(participant)
    resp = await client.put(f"/api/events/{event['id']}/rsvp", json={"going": True})
    assert resp.status_code == 400
