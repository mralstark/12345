"""Личный кабинет и назначение управленческой роли: только повышение уже
саморегистрированного человека из состава (services/admin_actions.py::
_resolve_or_promote) — назначить роль тому, кто ещё не проходил
саморегистрацию, нельзя. self-service правка своих же данных через
/api/profile."""

import pytest
from sqlalchemy import select

from database.models import ROLE_LEADER, ROLE_PARTICIPANT, Member, Region, User
from services.admin_actions import create_leader
from tests.conftest import login


@pytest.fixture
async def plain_member(session, world):
    """Человек в составе Москвы без какого-либо аккаунта — ещё не проходил
    саморегистрацию."""
    member = Member(region_id=world["moscow"].id, full_name="Волков Михаил")
    session.add(member)
    await session.commit()
    await session.refresh(member)
    return member


async def _self_register(session, member, telegram_id=555555):
    """То, что реально оставляет после себя саморегистрация
    (services/applications.py::approve_application) — участник с аккаунтом
    и уже известным telegram_id."""
    user = User(full_name=member.full_name, role=ROLE_PARTICIPANT, member_id=member.id, telegram_id=telegram_id)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def test_create_leader_rejects_member_without_account(session, world, plain_member):
    """Человек есть в «Составе», но ещё не саморегистрировался — назначить
    роль нельзя, пока он сам не пройдёт регистрацию по ссылке региона."""
    region = await session.get(Region, world["tula"].id)
    # Тула уже занята leader_tula — освобождаем, чтобы не путать «замену».
    region.leader_user_id = None
    await session.commit()

    with pytest.raises(ValueError):
        await create_leader(session, region.id, member_id=plain_member.id)


async def test_create_leader_promotes_existing_cabinet_in_place(session, world, plain_member):
    """У человека уже есть аккаунт (саморегистрация) — назначение
    руководителем не создаёт второй, а повышает роль на месте."""
    cabinet_user = await _self_register(session, plain_member)

    region = await session.get(Region, world["tula"].id)
    region.leader_user_id = None
    await session.commit()

    promoted = await create_leader(session, region.id, member_id=plain_member.id)

    assert promoted.id == cabinet_user.id  # тот же самый User, не новый
    assert promoted.role == ROLE_LEADER
    assert promoted.telegram_id == cabinet_user.telegram_id  # не тронут

    users_for_member = (
        await session.execute(select(User).where(User.member_id == plain_member.id))
    ).scalars().all()
    assert len(users_for_member) == 1


async def test_create_leader_rejects_promoting_superuser(session, world, plain_member):
    su_member = Member(region_id=world["moscow"].id, full_name="Другой Суперюзер")
    session.add(su_member)
    await session.flush()
    world["superuser"].member_id = su_member.id
    await session.commit()

    region = await session.get(Region, world["tula"].id)
    region.leader_user_id = None
    await session.commit()

    with pytest.raises(ValueError):
        await create_leader(session, region.id, member_id=su_member.id)


# --- API /profile -----------------------------------------------------------


async def test_profile_self_edit_persists_to_same_member(client, session, world, plain_member):
    user = await _self_register(session, plain_member)
    login(user)

    resp = await client.get("/api/profile/me")
    assert resp.status_code == 200
    assert resp.json()["full_name"] == plain_member.full_name
    assert resp.json()["region_name"] == "Москва"

    patched = await client.patch("/api/profile/me", json={"workplace": "Тестовая компания"})
    assert patched.status_code == 200
    assert patched.json()["workplace"] == "Тестовая компания"

    await session.refresh(plain_member)
    assert plain_member.workplace == "Тестовая компания"


async def test_profile_404_without_member_link(client, world):
    """superuser не привязан ни к одному Member — у него личного кабинета нет."""
    login(world["superuser"])
    resp = await client.get("/api/profile/me")
    assert resp.status_code == 404


async def test_context_me_reports_has_personal_cabinet(client, session, world, plain_member):
    user = await _self_register(session, plain_member)
    login(user)
    resp = await client.get("/api/me")
    assert resp.status_code == 200
    assert resp.json()["has_personal_cabinet"] is True
    assert resp.json()["role"] == ROLE_PARTICIPANT

    login(world["superuser"])
    resp2 = await client.get("/api/me")
    assert resp2.json()["has_personal_cabinet"] is False
