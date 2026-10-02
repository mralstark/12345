from datetime import timedelta

from sqlalchemy import select

from database.models import (
    Member, MemberQuestProgress, Quest, QuestActivity, User, Event, Task,
    NewsPost, ROLE_PARTICIPANT,
)
from scripts.migrate_gamification import sync_catalog
from services.news import create_news, news_audience_telegram_ids
from tests.conftest import login
from utils.tz import today


async def student(session, region_id, telegram_id=7101, status="activist"):
    member = Member(region_id=region_id, full_name="Тестов Академист", status=status)
    session.add(member)
    await session.flush()
    user = User(full_name=member.full_name, telegram_id=telegram_id, role=ROLE_PARTICIPANT, member_id=member.id)
    session.add(user)
    await session.commit()
    return member, user


async def test_assignment_can_be_edited_cancelled_and_reissued(client, session, world):
    member, owner = await student(session, world["moscow"].id)
    quest = Quest(title="Сыграть в килу", emoji="⚽", thresholds="1,3,10")
    session.add(quest)
    await session.commit()
    path = f"/api/members/{member.id}/quests/{quest.id}"
    login(world["leader_moscow"])
    assert (await client.post(path + "/assign", json={"note": "Сначала тренировка"})).status_code == 200
    edited = (await client.post(path + "/assign", json={"note": "Новый комментарий"})).json()
    assert edited["assignment_note"] == "Новый комментарий"
    cancelled = (await client.post(path + "/cancel")).json()
    assert not cancelled["assigned"] and cancelled["assignment_note"] is None
    assert cancelled["count"] == 0
    await client.post(path + "/assign", json={"note": "Повторная выдача"})
    login(owner)
    assert (await client.post(f"/api/character/me/quests/{quest.id}/submit", json={"note": "Готово"})).status_code == 200
    login(world["leader_moscow"])
    approved = (await client.post(path + "/increment")).json()
    assert approved["count"] == 1 and approved["pending_count"] == 0
    assert not approved["assigned"] and approved["assignment_note"] is None
    assert (await client.post(path + "/assign", json={"note": "Следующая тренировка"})).status_code == 200
    history = (await client.get(f"/api/members/{member.id}/quests")).json()["history"]
    assert {h["action"] for h in history} >= {"assigned", "updated", "cancelled", "submitted", "approved"}


async def test_one_time_quest_cannot_be_repeated_and_foreign_manager_denied(client, session, world):
    member, user = await student(session, world["moscow"].id)
    quest = Quest(title="Разовое", emoji="📌", thresholds="1")
    session.add(quest)
    await session.commit()
    path = f"/api/members/{member.id}/quests/{quest.id}"
    login(world["leader_tula"])
    assert (await client.post(path + "/assign", json={})).status_code == 403
    login(world["cell_leader"])
    assert (await client.get(f"/api/academy?region_id={member.region_id}")).status_code == 403
    login(world["leader_moscow"])
    assert (await client.post(path + "/increment")).status_code == 200
    assert (await client.post(path + "/increment")).status_code == 409
    assert (await client.post(path + "/assign", json={})).status_code == 409
    login(user)
    assert (await client.post(f"/api/character/me/quests/{quest.id}/submit", json={})).status_code == 409


async def test_full_members_keep_rewards_but_cannot_submit(client, session, world):
    member, user = await student(session, world["moscow"].id, status="member")
    quest = Quest(title="Награда", emoji="📌", thresholds="1")
    session.add(quest)
    await session.flush()
    session.add(MemberQuestProgress(member_id=member.id, quest_id=quest.id, count=1))
    await session.commit()
    login(user)
    assert not (await client.get("/api/me")).json()["academy_enabled"]
    assert not (await client.get("/api/character/me")).json()["academy_enabled"]
    assert (await client.post(f"/api/character/me/quests/{quest.id}/submit", json={})).status_code == 409
    assert (await client.post(f"/api/character/me/quests/{quest.id}/claim")).json()["stars"] == 1


async def test_catalog_migration_preserves_legacy_rewards_and_ids(client, session, world):
    member, user = await student(session, world["moscow"].id)
    book = Quest(title="Прочитать книгу", emoji="📚", thresholds="1,3")
    stickers = Quest(title="Расклеить стикеры", emoji="📌", thresholds="25,50,100,250,500", rewards="1,2,3,5,10")
    help_quest = Quest(title="Помочь другому участнику Братства", emoji="🤝", thresholds="1,3")
    session.add_all([book, stickers, help_quest])
    await session.flush()
    old_ids = (book.id, stickers.id, help_quest.id)
    session.add_all([
        MemberQuestProgress(member_id=member.id, quest_id=book.id, count=3, stars_claimed=1),
        MemberQuestProgress(member_id=member.id, quest_id=stickers.id, count=500, stars_claimed=3),
    ])
    await session.commit()
    await sync_catalog(session)
    await sync_catalog(session)
    assert (book.id, stickers.id, help_quest.id) == old_ids
    assert not book.is_active and help_quest.title == "Выручить корпоранта"
    assert stickers.thresholds == "1"
    all_quests = list((await session.execute(select(Quest))).scalars())
    assert len([q for q in all_quests if q.is_active]) == 15
    login(user)
    data = (await client.get("/api/character/me")).json()
    assert data["legacy_quests"][0]["claimable_stars"] == 3
    assert next(q for q in data["quests"] if q["id"] == stickers.id)["claimable_stars"] == 18
    assert (await client.post(f"/api/character/me/quests/{book.id}/claim")).json()["stars"] == 3
    assert (await client.post(f"/api/character/me/quests/{stickers.id}/claim")).json()["stars"] == 21
    assert (await client.post(f"/api/character/me/quests/{stickers.id}/claim")).status_code == 400


async def test_academy_pending_first_and_same_radar(client, session, world):
    first, user = await student(session, world["moscow"].id)
    second, _ = await student(session, world["moscow"].id, 7102)
    second.full_name = "Ааа Первый по алфавиту"
    quest = Quest(title="Сыграть в килу", emoji="⚽", thresholds="1,3,10")
    session.add(quest)
    await session.flush()
    session.add(MemberQuestProgress(member_id=first.id, quest_id=quest.id, count=3, pending_count=1))
    await session.commit()
    login(world["coordinator"])
    result = (await client.get(f"/api/academy?region_id={first.region_id}")).json()
    assert result["items"][0]["id"] == first.id
    assert (await client.get("/api/me")).json()["counters"]["regions"][str(first.region_id)]["academy"] == 1
    login(user)
    assert result["items"][0]["radar"] == (await client.get("/api/character/me")).json()["radar"]


async def test_personal_tasks_not_duplicated_and_calendar_region_scoped(client, session, world):
    member, owner = await student(session, world["moscow"].id)
    await session.delete(owner)
    await session.flush()
    world["coordinator"].member_id = member.id
    _, owner = await student(session, world["moscow"].id, 7103)
    foreign, other = await student(session, world["tula"].id, 7102)
    session.add_all([
        Task(from_user_id=world["federal"].id, to_user_id=world["coordinator"].id, title="Входящая", deadline=today()),
        Task(from_user_id=world["coordinator"].id, to_user_id=other.id, title="Чужой регион", deadline=today()),
        Task(from_user_id=world["coordinator"].id, to_user_id=owner.id, title="Свой регион", deadline=today()),
    ])
    await session.commit()
    login(world["coordinator"])
    assert len((await client.get("/api/tasks?personal=true")).json()["items"]) == 1
    assert (await client.get("/api/tasks?cabinet=management")).json()["items"] == []
    scoped = (await client.get(f"/api/tasks?box=outbox&cabinet=management&region_id={member.region_id}")).json()
    assert [t["title"] for t in scoped["items"]] == ["Свой регион"]
    assert (await client.get(f"/api/tasks?box=outbox&region_id={foreign.region_id}")).status_code == 403
    me = (await client.get("/api/me")).json()
    assert me["counters"]["new_tasks"] == 1 and me["counters"]["management_tasks"] == 0


async def test_regional_byline_scopes_notifications_and_cannot_escape_regions(client, session, world):
    _, first = await student(session, world["moscow"].id)
    _, second = await student(session, world["tula"].id, 7102)
    coordinator = world["coordinator"]
    post = await create_news(session, coordinator, "Сообщение региона", official=True, region_id=world["moscow"].id)
    assert post.byline == "Академисты | Москва" and post.byline_kind == "otdelenie"
    audience = await news_audience_telegram_ids(session, post)
    assert first.telegram_id in audience and second.telegram_id not in audience
    # Автору расширили области — уже опубликованный пост не меняет адресатов.
    coordinator.is_federal = True
    await session.commit()
    assert await news_audience_telegram_ids(session, post) == audience
    coordinator.is_federal = False
    await session.commit()
    login(coordinator)
    assert (await client.get(f"/api/news/byline?official=true&region_id={world['tula'].id}")).status_code == 403
    assert (await client.post("/api/news", data={"text":"Попытка", "official":"true", "region_id":world["tula"].id})).status_code == 403


async def test_seen_cursor_persists_and_never_swallows_later_records(client, session, world):
    member, user = await student(session, world["moscow"].id)
    event = Event(region_id=member.region_id, title="Новое событие", date=today()+timedelta(days=1))
    post = NewsPost(author_user_id=world["federal"].id, text="Новость")
    session.add_all([event, post])
    await session.commit()
    login(user)
    me = (await client.get("/api/me")).json()
    assert me["counters"]["personal_events"] == 1 and me["counters"]["news"] == 1
    event_cursor = (await client.get("/api/events/mine?scope=all")).json()["seen_cursor"]
    news_cursor = (await client.get("/api/news?personal=true")).json()["seen_cursor"]
    newer = NewsPost(author_user_id=world["federal"].id, text="Появилось после открытия")
    session.add(newer)
    await session.commit()
    assert (await client.post("/api/me/seen", json={"section":"news", "cursor":news_cursor})).status_code == 200
    assert (await client.post("/api/me/seen", json={"section":"events", "cursor":event_cursor})).status_code == 200
    me = (await client.get("/api/me")).json()
    assert me["counters"]["news"] == 1 and me["counters"]["personal_events"] == 0
    assert (await client.post("/api/me/seen", json={"section":"news", "cursor":999999})).status_code == 403
    assert (await client.post("/api/me/seen", json={"section":"events", "cursor":event_cursor, "region_id":world["tula"].id})).status_code == 403


async def test_coordinator_cannot_approve_own_academy_work(client, session, world):
    member, owner = await student(session, world["moscow"].id)
    await session.delete(owner)
    await session.flush()
    world["coordinator"].member_id = member.id
    quest = Quest(title="Сыграть в килу", emoji="⚽", thresholds="1,3,10")
    session.add(quest)
    await session.commit()
    login(world["coordinator"])
    path = f"/api/members/{member.id}/quests"
    assert not (await client.get(path)).json()["can_review"]
    assert (await client.post(f"{path}/{quest.id}/increment")).status_code == 403
    login(world["leader_moscow"])
    assert (await client.post(f"{path}/{quest.id}/increment")).status_code == 200


async def test_schema_migration_is_additive_and_repeatable(tmp_path, monkeypatch):
    from sqlalchemy import inspect, text
    from sqlalchemy.ext.asyncio import create_async_engine
    from database.models import Base
    from scripts import migrate_cabinet_revisions

    test_engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'old-schema.db').as_posix()}")
    try:
        async with test_engine.begin() as conn:
            for ddl in (
                "CREATE TABLE regions (id INTEGER PRIMARY KEY, name TEXT, is_active BOOLEAN)",
                "CREATE TABLE users (id INTEGER PRIMARY KEY)",
                "CREATE TABLE members (id INTEGER PRIMARY KEY)",
                "CREATE TABLE quests (id INTEGER PRIMARY KEY)",
                "CREATE TABLE tasks (id INTEGER PRIMARY KEY)",
                "CREATE TABLE news_posts (id INTEGER PRIMARY KEY, text TEXT)",
                "CREATE TABLE member_quest_progress (id INTEGER PRIMARY KEY, count INTEGER)",
            ):
                await conn.execute(text(ddl))
            await conn.execute(text("INSERT INTO regions (name, is_active) VALUES ('Сохранить регион', 1)"))
        monkeypatch.setattr(migrate_cabinet_revisions, "engine", test_engine)
        await migrate_cabinet_revisions.migrate()
        await migrate_cabinet_revisions.migrate()
        async with test_engine.begin() as conn:
            tables = await conn.run_sync(lambda c: inspect(c).get_table_names())
            assert {"section_reads", "quest_activity"} <= set(tables)
            assert (await conn.execute(text("SELECT name FROM regions"))).scalar() == "Сохранить регион"
            columns = await conn.run_sync(lambda c: {x['name'] for x in inspect(c).get_columns('member_quest_progress')})
            assert "earned_stars_floor" in columns
    finally:
        await test_engine.dispose()
