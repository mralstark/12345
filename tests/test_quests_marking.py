"""Отметка выполнения заданий — сторона руководителя.

Главное, что тут проверяется: руководитель видит, что сделал. Он не
начисляет звёзды — их человек забирает сам в «Академии», — и экран обязан
показывать и то, и другое, иначе выходит «нажал и непонятно, было ли».
"""

from database.models import (
    MEMBER_STATUS_ACTIVIST,
    ROLE_PARTICIPANT,
    Member,
    MemberQuestProgress,
    Quest,
    User,
)
from tests.conftest import login


async def _quest(session, thresholds="1,5,10", rewards="", title="Побывать на собрании"):
    quest = Quest(title=title, emoji="🏛", thresholds=thresholds, rewards=rewards, position=0)
    session.add(quest)
    await session.commit()
    await session.refresh(quest)
    return quest


async def _member(session, region_id, telegram_id=6001):
    member = Member(region_id=region_id, full_name="Активов Актив", status=MEMBER_STATUS_ACTIVIST)
    session.add(member)
    await session.flush()
    session.add(User(full_name="Активов Актив", role=ROLE_PARTICIPANT,
                     member_id=member.id, telegram_id=telegram_id))
    await session.commit()
    await session.refresh(member)
    return member


async def test_list_shows_the_balance(client, world, session):
    """Баланса человека руководитель не видел нигде — ни до, ни после нажатия."""
    quest = await _quest(session)
    member = await _member(session, world["moscow"].id)
    login(world["leader_moscow"])

    await client.post(f"/api/members/{member.id}/quests/{quest.id}/increment")
    data = (await client.get(f"/api/members/{member.id}/quests")).json()

    assert data["claimed"] == 0, "звёзды забирает сам человек, не руководитель"
    assert data["pending"] == 1, "ступень 1 пройдена — столько ему и откроется"


async def test_next_reward_equals_the_threshold(client, world, session):
    """Награда равна номеру ступени (_earned_stars суммирует сами пороги) —
    подпись «откроется N ★» должна брать её оттуда, а не выдумывать."""
    quest = await _quest(session)
    member = await _member(session, world["moscow"].id, 6002)
    login(world["leader_moscow"])

    item = (await client.get(f"/api/members/{member.id}/quests")).json()["items"][0]
    assert item["next_target"] == 1 and item["next_reward"] == 1

    await client.post(f"/api/members/{member.id}/quests/{quest.id}/increment")
    item = (await client.get(f"/api/members/{member.id}/quests")).json()["items"][0]
    assert item["next_target"] == 5 and item["next_reward"] == 5


async def test_a_quest_can_price_its_steps_apart_from_them(client, world, session):
    """Порог и цена — разные вещи.

    Обычно ступень стоит столько же, сколько её номер: сыграл в килу десять
    раз — получил десять. Но у «расклеить 500 стикеров» порог меряет объём
    работы, а не заслугу, и по общему правилу одно это задание выдало бы
    больше, чем весь остальной каталог вместе. Поэтому цену можно назначить
    отдельно — и подпись «откроется N ★» обязана брать именно её.
    """
    quest = await _quest(
        session, thresholds="25,50,100", rewards="1,2,3", title="Расклеить стикеры"
    )
    member = await _member(session, world["moscow"].id, 6007)
    login(world["leader_moscow"])

    item = (await client.get(f"/api/members/{member.id}/quests")).json()["items"][0]
    assert item["next_target"] == 25, "цель — двадцать пять стикеров"
    assert item["next_reward"] == 1, "а стоит эта ступень одну звезду, не двадцать пять"

    for _ in range(25):
        await client.post(f"/api/members/{member.id}/quests/{quest.id}/increment")

    data = (await client.get(f"/api/members/{member.id}/quests")).json()
    assert data["pending"] == 1, "за первую ступень — одна звезда, а не двадцать пять"
    item = data["items"][0]
    assert item["next_target"] == 50 and item["next_reward"] == 2


async def test_a_quest_without_its_own_price_pays_the_threshold(client, world, session):
    """Прежние задания не тронуты: без своей цены ступень стоит как её порог."""
    quest = await _quest(session, thresholds="1,5,10")
    member = await _member(session, world["moscow"].id, 6008)
    login(world["leader_moscow"])

    for _ in range(5):
        await client.post(f"/api/members/{member.id}/quests/{quest.id}/increment")

    assert (await client.get(f"/api/members/{member.id}/quests")).json()["pending"] == 6, "1 + 5"


async def test_mistaken_mark_can_be_taken_back(client, world, session):
    """Промахнуться по «Отметить» легко, а отменить было нечем — оставалось
    только лезть в базу."""
    quest = await _quest(session)
    member = await _member(session, world["moscow"].id, 6003)
    login(world["leader_moscow"])

    await client.post(f"/api/members/{member.id}/quests/{quest.id}/increment")
    await client.post(f"/api/members/{member.id}/quests/{quest.id}/increment")
    assert (await client.get(f"/api/members/{member.id}/quests")).json()["items"][0]["count"] == 2

    response = await client.post(f"/api/members/{member.id}/quests/{quest.id}/decrement")

    assert response.status_code == 200
    assert response.json()["count"] == 1


async def test_undo_never_goes_below_zero(client, world, session):
    """Отрицательный счётчик сломал бы и полоску, и подсчёт звёзд."""
    quest = await _quest(session)
    member = await _member(session, world["moscow"].id, 6004)
    login(world["leader_moscow"])

    await client.post(f"/api/members/{member.id}/quests/{quest.id}/decrement")
    await client.post(f"/api/members/{member.id}/quests/{quest.id}/decrement")

    data = (await client.get(f"/api/members/{member.id}/quests")).json()
    assert data["items"][0]["count"] == 0
    assert data["pending"] == 0


async def test_claimed_stars_are_not_taken_back(client, world, session):
    """Что человек получил — то получил. После отмены «ждёт в Академии» уходит
    в ноль, но не в минус, и забранное остаётся за ним."""
    quest = await _quest(session)
    member = await _member(session, world["moscow"].id, 6005)
    login(world["leader_moscow"])

    await client.post(f"/api/members/{member.id}/quests/{quest.id}/increment")
    # человек забрал звёзды в «Академии»
    row = (await session.execute(
        MemberQuestProgress.__table__.select().where(MemberQuestProgress.member_id == member.id)
    )).first()
    progress = await session.get(MemberQuestProgress, row.id)
    progress.stars_claimed = 1
    await session.commit()

    await client.post(f"/api/members/{member.id}/quests/{quest.id}/decrement")

    data = (await client.get(f"/api/members/{member.id}/quests")).json()
    assert data["items"][0]["count"] == 0
    assert data["pending"] == 0, "минус звёзд быть не может"
    assert data["claimed"] == 1, "забранное остаётся за человеком"


async def test_undo_says_nothing_to_the_person(client, world, session, monkeypatch):
    """Отметка о переходе ступени уходит в бот, а её отмена — нет: человеку
    незачем узнавать, что у него отняли уровень. Это правка руководителя."""
    sent = []

    async def fake_send(telegram_id, text, **kwargs):
        sent.append(text)
        return None

    monkeypatch.setattr("api.routers.quests.notify_telegram", fake_send)
    quest = await _quest(session)
    member = await _member(session, world["moscow"].id, 6006)
    login(world["leader_moscow"])

    await client.post(f"/api/members/{member.id}/quests/{quest.id}/increment")
    assert len(sent) == 1, "о пройденной ступени сказать надо"

    await client.post(f"/api/members/{member.id}/quests/{quest.id}/decrement")
    assert len(sent) == 1, "об отмене — не надо"


async def test_stranger_cannot_mark(client, world, session):
    """Отменять может тот же, кто отмечает, — и не в чужом отделении."""
    quest = await _quest(session)
    member = await _member(session, world["moscow"].id, 6007)
    login(world["leader_tula"])

    assert (await client.post(
        f"/api/members/{member.id}/quests/{quest.id}/decrement")).status_code == 403
