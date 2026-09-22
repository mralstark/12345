"""Заявка на вступление — services/applications.py (одобрение/отклонение) и
Region.application_code (services/admin_actions.create_region/regenerate).
Бот-хендлеры (handlers/apply.py) не юнит-тестируются напрямую — как и
остальные FSM-диалоги в этом проекте (были в create_account.py,
manage_accounts.py — оба с тех пор удалены), проверяется бизнес-логика в
сервисном слое, которую они вызывают."""

import pytest
from sqlalchemy import select

from database.models import (
    APPLICATION_STATE_APPROVED,
    APPLICATION_STATE_PENDING,
    APPLICATION_STATE_REJECTED,
    Member,
    MembershipApplication,
    University,
    UniversityCell,
    User,
)
from services.admin_actions import create_region, regenerate_application_code
from services.applications import approve_application, reject_application
from tests.conftest import login


@pytest.fixture
async def application(session, world):
    app = MembershipApplication(
        region_id=world["moscow"].id,
        telegram_id=777001,
        full_name="Новиков Никита",
        phone="+7 900 111-22-33",
    )
    session.add(app)
    await session.commit()
    await session.refresh(app)
    return app


async def test_create_region_generates_application_code(session, world):
    region = await create_region(session, "Регион с кодом заявки")
    assert region.application_code
    assert len(region.application_code) >= 8


async def test_regenerate_application_code_changes_value(session, world):
    old_code = world["moscow"].application_code
    updated = await regenerate_application_code(session, world["moscow"].id)
    assert updated.application_code != old_code
    assert updated.application_code


async def test_approve_creates_active_member_and_user(session, world, application):
    user = await approve_application(session, application.id, world["leader_moscow"].id)

    assert user.telegram_id == 777001
    assert user.role == "participant"

    member = await session.get(Member, user.member_id)
    assert member is not None
    assert member.full_name == "Новиков Никита"
    assert member.region_id == world["moscow"].id
    assert member.status == "activist"  # по умолчанию у фикстуры application ниже

    await session.refresh(application)
    assert application.state == APPLICATION_STATE_APPROVED
    assert application.reviewed_by_user_id == world["leader_moscow"].id
    assert application.reviewed_at is not None


async def test_approve_carries_chosen_status_and_education_level(session, world):
    """Статус и уровень обучения — выбор самого заявителя при регистрации
    (webapp/app.js::renderRegisterView), не то, что руководитель проставляет
    при одобрении — approve_application должен перенести их как есть."""
    app = MembershipApplication(
        region_id=world["moscow"].id,
        telegram_id=777003,
        full_name="Выпускников Егор",
        member_status="alumni",
        education_level="master",
    )
    session.add(app)
    await session.commit()
    await session.refresh(app)

    user = await approve_application(session, app.id, world["leader_moscow"].id)
    member = await session.get(Member, user.member_id)
    assert member.status == "alumni"
    assert member.education_level == "master"


async def test_approve_auto_creates_cell_by_university(session, world):
    """Ячейка производная от вуза (utils/university_cells.py) — при одобрении
    заявки с вузом, у которого ещё нет ячейки в этом регионе, она заводится
    сама, той же логикой, что и при вводе человека через «Состав»."""
    university = University(name="Тестовый Университет Для Заявки")
    session.add(university)
    await session.commit()
    await session.refresh(university)

    app = MembershipApplication(
        region_id=world["moscow"].id,
        telegram_id=777002,
        full_name="Сидорова Анна",
        university_id=university.id,
    )
    session.add(app)
    await session.commit()
    await session.refresh(app)

    user = await approve_application(session, app.id, world["leader_moscow"].id)
    member = await session.get(Member, user.member_id)

    assert member.cell_id is not None
    cell = await session.get(UniversityCell, member.cell_id)
    assert cell.university_id == university.id
    assert cell.region_id == world["moscow"].id


async def test_approve_rejects_already_reviewed(session, world, application):
    await approve_application(session, application.id, world["leader_moscow"].id)
    with pytest.raises(ValueError):
        await approve_application(session, application.id, world["leader_moscow"].id)


async def test_reject_does_not_create_member(session, world, application):
    result = await reject_application(session, application.id, world["leader_moscow"].id)

    assert result.state == APPLICATION_STATE_REJECTED
    members = (
        await session.execute(select(Member).where(Member.full_name == application.full_name))
    ).scalars().all()
    assert members == []

    users = (await session.execute(select(User).where(User.telegram_id == 777001))).scalars().all()
    assert users == []


async def test_reject_rejects_already_reviewed(session, world, application):
    await reject_application(session, application.id, world["leader_moscow"].id)
    with pytest.raises(ValueError):
        await reject_application(session, application.id, world["leader_moscow"].id)


async def test_pending_state_default(application):
    assert application.state == APPLICATION_STATE_PENDING


async def test_superuser_can_edit_pending_application(client, session, world, application):
    """Вкладка «Заявки» в Mini App (api/routers/applications.py) — правка тех
    же полей, что и «Исправить» в боте (handlers/apply.py::_EDIT_FIELDS)."""
    login(world["superuser"])
    response = await client.patch(
        f"/api/applications/{application.id}",
        json={"full_name": "Исправленное Имя", "faculty": "Новый факультет"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["full_name"] == "Исправленное Имя"

    await session.refresh(application)
    assert application.full_name == "Исправленное Имя"
    assert application.faculty == "Новый факультет"
    # Не тронутые поля не сбрасываются — PATCH частичный.
    assert application.phone == "+7 900 111-22-33"


async def test_region_leader_can_edit_own_application(client, world, application):
    login(world["leader_moscow"])
    response = await client.patch(f"/api/applications/{application.id}", json={"full_name": "Кто-то"})
    assert response.status_code == 200


async def test_cannot_edit_already_reviewed_application(client, session, world, application):
    login(world["superuser"])
    await client.post(f"/api/applications/{application.id}/reject")
    response = await client.patch(f"/api/applications/{application.id}", json={"full_name": "Поздно"})
    assert response.status_code == 400
