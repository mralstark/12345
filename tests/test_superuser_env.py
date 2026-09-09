"""SUPERUSER_TELEGRAM_IDS — список тех, кому доступ открыт без приглашения.

Он задуман двояко: завести первого человека в пустой системе, где пригласить
его некому, и назначить админом того, кто уже зарегистрировался. Второе долго
не работало: resolve_user спрашивал про список только тогда, когда обычный
поиск ничего не нашёл, — то есть до человека с учётной записью очередь не
доходила. Вписать участника в .env было можно, толку — никакого.
"""

from database.models import ROLE_PARTICIPANT, ROLE_SUPERUSER, Member, User
from utils import users as users_module


async def _participant(session, region_id, name, telegram_id):
    member = Member(region_id=region_id, full_name=name)
    session.add(member)
    await session.flush()
    user = User(full_name=name, role=ROLE_PARTICIPANT, member_id=member.id, telegram_id=telegram_id)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def test_listed_id_promotes_an_existing_account(session, world, monkeypatch):
    """Уже зарегистрированный участник становится админом — и остаётся собой.

    Карточка в «Составе» при этом не трогается: админство это роль, а не
    новый человек. Иначе у него пропал бы личный кабинет.
    """
    person = await _participant(session, world["moscow"].id, "Старков Алексей", 2027003868)
    monkeypatch.setattr(users_module, "SUPERUSER_TELEGRAM_IDS", {2027003868})

    resolved = await users_module.resolve_user(session, 2027003868, "Старков Алексей")

    assert resolved is not None
    assert resolved.id == person.id, "это тот же человек, а не новая запись"
    assert resolved.role == ROLE_SUPERUSER
    assert resolved.member_id == person.member_id, "карточка в составе осталась при нём"


async def test_unlisted_account_is_left_alone(session, world, monkeypatch):
    """Никого лишнего список не задевает."""
    person = await _participant(session, world["moscow"].id, "Обычный Участник", 2027003869)
    monkeypatch.setattr(users_module, "SUPERUSER_TELEGRAM_IDS", {2027003868})

    resolved = await users_module.resolve_user(session, 2027003869, "Обычный Участник")

    assert resolved.id == person.id
    assert resolved.role == ROLE_PARTICIPANT


async def test_listed_id_without_an_account_still_gets_one(session, monkeypatch):
    """Прежнее назначение списка цело: в пустой системе он заводит первого."""
    monkeypatch.setattr(users_module, "SUPERUSER_TELEGRAM_IDS", {2027003870})

    created = await users_module.resolve_user(session, 2027003870, "Первый Админ")

    assert created is not None
    assert created.role == ROLE_SUPERUSER
    assert created.member_id is None, "отделений ещё нет — карточку класть некуда"


async def test_stranger_without_an_account_gets_nothing(session, monkeypatch):
    monkeypatch.setattr(users_module, "SUPERUSER_TELEGRAM_IDS", {2027003870})

    assert await users_module.resolve_user(session, 999999999, "Посторонний") is None
