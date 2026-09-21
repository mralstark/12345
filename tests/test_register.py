"""Регистрация — веб-форма Mini App вместо чата (api/routers/register.py,
план «Снизу вверх», §2). Настоящий отбор идёт на внешнем сайте (вне этого
проекта) — здесь только анкета уже принятого человека."""

from sqlalchemy import select

import api.routers.register as register_module
from database.models import (
    APPLICATION_STATE_APPROVED,
    APPLICATION_STATE_PENDING,
    MembershipApplication,
    University,
    User,
)
from tests.conftest import login_as_identity


async def test_context_lists_regions_for_unknown_identity(client, world):
    login_as_identity(999001)
    response = await client.get("/api/register/context")
    assert response.status_code == 200
    data = response.json()
    assert data["already_registered"] is False
    labels = {r["label"] for r in data["regions"]}
    # Единый формат подписи региона (utils/roles.py::region_display_name) —
    # без родительного падежа, всегда "Академисты | Название".
    assert "Академисты | Москва" in labels


async def test_context_reports_already_registered(client, world):
    login_as_identity(world["leader_moscow"].telegram_id, world["leader_moscow"].full_name)
    response = await client.get("/api/register/context")
    assert response.json() == {"already_registered": True}


async def test_universities_requires_region_id(client, world):
    login_as_identity(999002)
    response = await client.get("/api/register/universities")
    assert response.status_code == 422


async def test_universities_scoped_to_chosen_region(client, session, world):
    moscow_uni = University(name="МГУ им. М.В. Ломоносова", region_id=world["moscow"].id)
    tula_uni = University(name="Тульский государственный университет", region_id=world["tula"].id)
    session.add_all([moscow_uni, tula_uni])
    await session.commit()

    login_as_identity(999003)
    response = await client.get(f"/api/register/universities?region_id={world['moscow'].id}")
    names = [u["name"] for u in response.json()["items"]]
    assert names == ["МГУ им. М.В. Ломоносова"]


async def test_public_registration_cannot_modify_university_catalog(client, session, world):
    login_as_identity(999004)
    response = await client.post(
        "/api/register/universities", json={"name": "Новый Институт", "region_id": world["moscow"].id}
    )
    assert response.status_code == 403, response.text
    university = (
        await session.execute(select(University).where(University.name == "Новый Институт"))
    ).scalar_one_or_none()
    assert university is None


async def _moscow_university(session, world) -> University:
    university = University(name="МГУ им. М.В. Ломоносова", region_id=world["moscow"].id)
    session.add(university)
    await session.commit()
    await session.refresh(university)
    return university


async def test_submit_creates_pending_application(client, session, world):
    university = await _moscow_university(session, world)
    login_as_identity(999005, "Новиков Никита")
    payload = {
        "full_name": "Новиков Никита",
        "birth_date": "20.02.2000",
        "phone": "+7 900 111-22-33",
        "telegram_username": "@novikov",
        "region_id": world["moscow"].id,
        "university_id": university.id,
        "faculty": "Экономический",
        "course": 2,
        "education_level": "bachelor",
        "status": "activist",
    }
    response = await client.post("/api/register/submit", json=payload)
    assert response.status_code == 200, response.text

    application = (
        await session.execute(select(MembershipApplication).where(MembershipApplication.telegram_id == 999005))
    ).scalar_one()
    assert application.state == APPLICATION_STATE_PENDING
    assert application.full_name == "Новиков Никита"
    assert application.course == 2
    assert application.education_level == "bachelor"
    assert application.member_status == "activist"
    assert application.birth_date.isoformat() == "2000-02-20"
    assert application.telegram_username == "@novikov"


async def test_submit_can_add_missing_university_atomically(client, session, world):
    login_as_identity(999105, "Новый Пользователь")
    response = await client.post(
        "/api/register/submit",
        json={
            "full_name": "Новый Пользователь",
            "birth_date": "20.02.2000",
            "phone": "+7 900 111-22-33",
            "telegram_username": "@new_user",
            "region_id": world["moscow"].id,
            "university_name": "  Новый   государственный вуз  ",
            "faculty": "Экономический",
            "course": 2,
            "education_level": "bachelor",
            "status": "activist",
        },
    )
    assert response.status_code == 200, response.text

    university = (
        await session.execute(select(University).where(University.name == "Новый государственный вуз"))
    ).scalar_one()
    assert university.region_id == world["moscow"].id
    application = (
        await session.execute(select(MembershipApplication).where(MembershipApplication.telegram_id == 999105))
    ).scalar_one()
    assert application.university_id == university.id


async def test_invalid_application_does_not_add_missing_university(client, session, world):
    login_as_identity(999106, "Новый Пользователь")
    response = await client.post(
        "/api/register/submit",
        json={
            "full_name": "Новый Пользователь",
            "birth_date": "не дата",
            "phone": "+7 900",
            "telegram_username": "@new_user2",
            "region_id": world["moscow"].id,
            "university_name": "Вуз из невалидной заявки",
            "faculty": "Экономический",
            "course": 2,
            "education_level": "bachelor",
            "status": "activist",
        },
    )
    assert response.status_code == 400
    university = (
        await session.execute(select(University).where(University.name == "Вуз из невалидной заявки"))
    ).scalar_one_or_none()
    assert university is None


async def test_submit_rejects_all_fields_required(client, world):
    """Все поля анкеты обязательны — сокращать её дальше некуда, она и так
    короче внешней формы отбора на сайте."""
    login_as_identity(999008, "Пропусков Пётр")
    payload = {
        "full_name": "Пропусков Пётр",
        "birth_date": "01.01.2001",
        "phone": "+7 900",
        "region_id": world["moscow"].id,
        # university_id/faculty/course намеренно не заполнены.
    }
    response = await client.post("/api/register/submit", json=payload)
    assert response.status_code == 422


async def test_submit_graduated_requires_workplace(client, session, world):
    university = await _moscow_university(session, world)
    login_as_identity(999020, "Окончивший Олег")
    base = {
        "full_name": "Окончивший Олег",
        "birth_date": "01.01.1999",
        "phone": "+7 900",
        "telegram_username": "@oleg",
        "region_id": world["moscow"].id,
        "university_id": university.id,
        "faculty": "Юридический",
        "graduated_university": True,
        "education_level": "bachelor",
        "status": "alumni",
    }
    missing_workplace = await client.post("/api/register/submit", json=base)
    assert missing_workplace.status_code == 400

    with_workplace = await client.post("/api/register/submit", json={**base, "workplace": "ООО Ромашка"})
    assert with_workplace.status_code == 200, with_workplace.text

    application = (
        await session.execute(select(MembershipApplication).where(MembershipApplication.telegram_id == 999020))
    ).scalar_one()
    assert application.course is None
    assert application.graduated_university is True
    assert application.workplace == "ООО Ромашка"


async def test_submit_rejects_unknown_education_level_or_status(client, session, world):
    university = await _moscow_university(session, world)
    base = {
        "full_name": "Некорректов Некто",
        "birth_date": "01.01.2001",
        "phone": "+7 900",
        "telegram_username": "@nekto",
        "region_id": world["moscow"].id,
        "university_id": university.id,
        "faculty": "Юридический",
        "course": 1,
    }

    login_as_identity(999011)
    bad_level = await client.post(
        "/api/register/submit", json={**base, "education_level": "phd", "status": "activist"}
    )
    assert bad_level.status_code == 400

    login_as_identity(999012)
    bad_status = await client.post(
        "/api/register/submit", json={**base, "education_level": "bachelor", "status": "vip"}
    )
    assert bad_status.status_code == 400


async def test_submit_rejects_duplicate_pending(client, session, world):
    university = await _moscow_university(session, world)
    login_as_identity(999006, "Дубликатов Данила")
    payload = {
        "full_name": "Дубликатов Данила",
        "birth_date": "01.01.2001",
        "phone": "+7 900",
        "telegram_username": "@danila",
        "region_id": world["moscow"].id,
        "university_id": university.id,
        "faculty": "Юридический",
        "course": 1,
        "education_level": "bachelor",
        "status": "activist",
    }
    first = await client.post("/api/register/submit", json=payload)
    assert first.status_code == 200
    second = await client.post("/api/register/submit", json=payload)
    assert second.status_code == 409


async def test_submit_rejects_unparseable_birth_date(client, session, world):
    university = await _moscow_university(session, world)
    login_as_identity(999007)
    response = await client.post(
        "/api/register/submit",
        json={
            "full_name": "Иванов Иван",
            "birth_date": "не дата",
            "phone": "+7 900",
            "telegram_username": "@ivan",
            "region_id": world["moscow"].id,
            "university_id": university.id,
            "faculty": "Юридический",
            "course": 1,
            "education_level": "bachelor",
            "status": "activist",
        },
    )
    assert response.status_code == 400


async def test_submit_rejects_when_already_has_account(client, session, world):
    university = await _moscow_university(session, world)
    login_as_identity(world["leader_moscow"].telegram_id, world["leader_moscow"].full_name)
    response = await client.post(
        "/api/register/submit",
        json={
            "full_name": "Кто-то",
            "birth_date": "01.01.2000",
            "phone": "+7 900",
            "telegram_username": "@ktoto",
            "region_id": world["moscow"].id,
            "university_id": university.id,
            "faculty": "Юридический",
            "course": 1,
            "education_level": "bachelor",
            "status": "activist",
        },
    )
    assert response.status_code == 409


async def test_submit_auto_approves_and_promotes_allowlisted_telegram_id(client, session, world, monkeypatch):
    """Админское доверие связано с Telegram ID из подписанного initData,
    а не с ФИО, которое заявитель заполняет самостоятельно."""
    monkeypatch.setattr(register_module, "AUTO_FEDERAL_TELEGRAM_IDS", {999010})
    university = await _moscow_university(session, world)

    login_as_identity(999010, "Тестов Админ Админович")
    response = await client.post(
        "/api/register/submit",
        json={
            "full_name": "Тестов Админ Админович",
            "birth_date": "01.01.1990",
            "phone": "+7 900",
            "telegram_username": "@admin",
            "region_id": world["moscow"].id,
            "university_id": university.id,
            "faculty": "Юридический",
            "course": 1,
            "education_level": "bachelor",
            "status": "activist",
        },
    )
    assert response.status_code == 200, response.text

    user = (await session.execute(select(User).where(User.telegram_id == 999010))).scalar_one()
    assert user.role == "federal"
    assert user.member_id is not None

    application = (
        await session.execute(select(MembershipApplication).where(MembershipApplication.telegram_id == 999010))
    ).scalar_one()
    assert application.state == APPLICATION_STATE_APPROVED
    assert application.reviewed_by_user_id is None


async def test_trusted_name_does_not_grant_federal_role(client, session, world, monkeypatch):
    monkeypatch.setattr(register_module, "AUTO_FEDERAL_TELEGRAM_IDS", {777777})
    university = await _moscow_university(session, world)
    login_as_identity(999011, "Тестов Админ Админович")

    response = await client.post(
        "/api/register/submit",
        json={
            "full_name": "Тестов Админ Админович",
            "birth_date": "01.01.1990",
            "phone": "+7 900",
            "telegram_username": "@admin2",
            "region_id": world["moscow"].id,
            "university_id": university.id,
            "faculty": "Юридический",
            "course": 1,
            "education_level": "bachelor",
            "status": "activist",
        },
    )
    assert response.status_code == 200
    application = (
        await session.execute(select(MembershipApplication).where(MembershipApplication.telegram_id == 999011))
    ).scalar_one()
    assert application.state == APPLICATION_STATE_PENDING


async def test_submit_rejects_university_from_another_region(client, session, world):
    university = University(name="Тульский вуз", region_id=world["tula"].id)
    session.add(university)
    await session.commit()
    login_as_identity(999012)

    response = await client.post(
        "/api/register/submit",
        json={
            "full_name": "Проверяемый Пользователь",
            "birth_date": "01.01.2000",
            "phone": "+7 900",
            "telegram_username": "@checked",
            "region_id": world["moscow"].id,
            "university_id": university.id,
            "faculty": "Юридический",
            "course": 1,
            "education_level": "bachelor",
            "status": "activist",
        },
    )
    assert response.status_code == 400
    assert "не принадлежит" in response.json()["detail"]
