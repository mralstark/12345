"""«Войти как» (utils/users.py) — доступно и superuser, и federal
(IMPERSONATOR_ROLES, план «Убираем технического superuser»). Проверяем ровно
то, что легко сломать незаметно: подмену идентичности через единую точку
входа resolve_user, запреты (нельзя войти как другой superuser/
несуществующего/отключённого), и что выход по-настоящему сохраняется, а не
теряется между сессиями."""

import pytest

from database.models import MEMBER_STATUS_ACTIVIST, MEMBER_STATUS_ALUMNI, ROLE_PARTICIPANT, Member, User
from tests.conftest import login
from utils.users import (
    candidate_context,
    impersonation_candidates,
    resolve_user,
    start_impersonation,
    stop_impersonation,
)


async def test_resolve_user_returns_target_when_impersonating(session, world):
    superuser = world["superuser"]
    target = await start_impersonation(session, superuser, world["leader_moscow"].id)
    assert target.id == world["leader_moscow"].id

    resolved = await resolve_user(session, superuser.telegram_id)
    assert resolved.id == world["leader_moscow"].id
    assert resolved._impersonated_by.id == superuser.id


async def test_resolve_user_is_normal_without_impersonation(session, world):
    resolved = await resolve_user(session, world["superuser"].telegram_id)
    assert resolved.id == world["superuser"].id
    assert getattr(resolved, "_impersonated_by", None) is None


async def test_stop_impersonation_restores_own_identity(session, world):
    superuser = world["superuser"]
    await start_impersonation(session, superuser, world["cell_leader"].id)
    await stop_impersonation(session, superuser)

    resolved = await resolve_user(session, superuser.telegram_id)
    assert resolved.id == superuser.id
    assert getattr(resolved, "_impersonated_by", None) is None


async def test_federal_can_also_impersonate(session, world):
    federal = world["federal"]
    target = await start_impersonation(session, federal, world["leader_tula"].id)
    assert target.id == world["leader_tula"].id

    resolved = await resolve_user(session, federal.telegram_id)
    assert resolved.id == world["leader_tula"].id
    assert resolved._impersonated_by.id == federal.id


async def test_cannot_impersonate_another_superuser(session, world):
    other_superuser = User(full_name="Второй Админ", role="superuser", telegram_id=1099)
    session.add(other_superuser)
    await session.commit()
    await session.refresh(other_superuser)

    with pytest.raises(ValueError):
        await start_impersonation(session, world["superuser"], other_superuser.id)


async def test_cannot_impersonate_unknown_or_inactive_user(session, world):
    with pytest.raises(ValueError):
        await start_impersonation(session, world["superuser"], 999_999)

    world["leader_tula"].is_active = False
    await session.commit()
    with pytest.raises(ValueError):
        await start_impersonation(session, world["superuser"], world["leader_tula"].id)


async def test_impersonation_candidates_group_participants_by_status(session, world):
    """Обычные участники все имеют role=participant — группа в пикере «Войти
    как...» строится по Member.status (activist/member/alumni), не по роли."""
    activist_member = Member(region_id=world["moscow"].id, full_name="Активист Активистов", status=MEMBER_STATUS_ACTIVIST)
    alumni_member = Member(region_id=world["moscow"].id, full_name="Выпускник Выпускников", status=MEMBER_STATUS_ALUMNI)
    session.add_all([activist_member, alumni_member])
    await session.flush()

    activist_user = User(full_name=activist_member.full_name, role=ROLE_PARTICIPANT, member_id=activist_member.id, telegram_id=444001)
    alumni_user = User(full_name=alumni_member.full_name, role=ROLE_PARTICIPANT, member_id=alumni_member.id, telegram_id=444002)
    session.add_all([activist_user, alumni_user])
    await session.commit()

    activists = await impersonation_candidates(session, f"status:{MEMBER_STATUS_ACTIVIST}")
    assert [u.id for u in activists] == [activist_user.id]

    alumni = await impersonation_candidates(session, f"status:{MEMBER_STATUS_ALUMNI}")
    assert [u.id for u in alumni] == [alumni_user.id]

    context = await candidate_context(session, activist_user)
    assert context == world["moscow"].name


async def test_federal_can_impersonate_ordinary_participant(session, world):
    member = Member(region_id=world["moscow"].id, full_name="Участник Обычный", status=MEMBER_STATUS_ACTIVIST)
    session.add(member)
    await session.flush()
    participant = User(full_name=member.full_name, role=ROLE_PARTICIPANT, member_id=member.id, telegram_id=444003)
    session.add(participant)
    await session.commit()

    federal = world["federal"]
    target = await start_impersonation(session, federal, participant.id)
    assert target.id == participant.id

    resolved = await resolve_user(session, federal.telegram_id)
    assert resolved.id == participant.id


async def test_api_me_reflects_impersonation_and_stop_persists(client, session, world):
    """Регресс: /me/stop-impersonation обязан сохраняться по-настоящему, а не
    только в памяти detached-объекта из уже закрытой сессии get_current_user."""
    superuser = world["superuser"]
    await start_impersonation(session, superuser, world["leader_moscow"].id)

    login(await resolve_user(session, superuser.telegram_id))
    me = await client.get("/api/me")
    assert me.json()["id"] == world["leader_moscow"].id
    assert me.json()["impersonated_by"] == superuser.full_name

    stopped = await client.post("/api/me/stop-impersonation")
    assert stopped.status_code == 200

    # Эндпоинт менял и коммитил через свою собственную сессию (Depends(get_db)),
    # а не через session этого теста — без явного refresh тестовая сессия
    # отдаст закешированный (устаревший) объект из своей identity-map, а не
    # то, что реально лежит в БД. session.refresh() форсирует перечитать.
    await session.refresh(superuser)
    assert superuser.view_as_user_id is None

    resolved_again = await resolve_user(session, superuser.telegram_id)
    assert resolved_again.id == superuser.id
    assert getattr(resolved_again, "_impersonated_by", None) is None
