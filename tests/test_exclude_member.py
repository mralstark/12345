"""«Исключить» — одно и то же действие для человека без личного кабинета и
с ним: полное, необратимое физическое удаление (services/admin_actions.py::
exclude_member, план «Снизу вверх», §5), не два разных уровня строгости, как
было раньше (мягкое is_active=False)."""

from datetime import date

import pytest
from sqlalchemy import select

from database.models import (
    ROLE_PARTICIPANT,
    Base,
    BirthdayNotice,
    BureauMember,
    CoordinatorRegion,
    Document,
    Event,
    EventAttendance,
    EventTask,
    EventTaskAssignee,
    Member,
    MemberQuestProgress,
    MembershipApplication,
    NewsComment,
    NewsPhoto,
    NewsPost,
    NewsReaction,
    NewsView,
    Quest,
    Region,
    ShopPurchase,
    Task,
    Transaction,
    UniversityCell,
    User,
)
from services import admin_actions
from services.admin_actions import (
    EXCLUDE_HANDLES_MEMBER_REFS,
    EXCLUDE_HANDLES_USER_REFS,
    exclude_member,
)
from tests.conftest import login


def _refs_to(target: str) -> set[str]:
    found = set()
    for table in Base.metadata.tables.values():
        for column in table.columns:
            for fk in column.foreign_keys:
                if str(fk.target_fullname) == target:
                    found.add(f"{table.name}.{column.name}")
    return found


def test_every_reference_to_a_person_is_accounted_for():
    """Схема и перечень в exclude_member должны совпадать ровно.

    Перечень ссылок писался руками и однажды отстал от схемы на пять таблиц:
    исключение падало пятисотой на news_views. Считать их глазами нельзя —
    пусть считает тест. Если он упал, в схеме появилась (или исчезла) ссылка
    на человека: впишите её в EXCLUDE_HANDLES_* и решите, обнулять её при
    исключении или удалять строку. Молчаливого «забыли» тут быть не должно.
    """
    assert _refs_to("users.id") == set(EXCLUDE_HANDLES_USER_REFS)
    assert _refs_to("members.id") == set(EXCLUDE_HANDLES_MEMBER_REFS)


async def test_exclude_member_removes_avatar_and_authored_news_files(
    session, world, tmp_path, monkeypatch
):
    monkeypatch.setattr(admin_actions, "STORAGE_DIR", tmp_path)
    avatar = tmp_path / "avatars" / "person.jpg"
    photo = tmp_path / "news" / "post.jpg"
    avatar.parent.mkdir(parents=True)
    photo.parent.mkdir(parents=True)
    avatar.write_bytes(b"avatar")
    photo.write_bytes(b"photo")

    member = Member(
        region_id=world["moscow"].id,
        full_name="Удаляемый Автор",
        avatar_path="avatars/person.jpg",
    )
    session.add(member)
    await session.flush()
    user = User(
        full_name=member.full_name,
        role=ROLE_PARTICIPANT,
        member_id=member.id,
        telegram_id=555099,
    )
    session.add(user)
    await session.flush()
    post = NewsPost(author_user_id=user.id, text="Новость", byline="Автор")
    session.add(post)
    await session.flush()
    session.add(
        NewsPhoto(
            post_id=post.id,
            stored_path="news/post.jpg",
            original_name="post.jpg",
            content_type="image/jpeg",
            size_bytes=5,
        )
    )
    await session.commit()

    await exclude_member(session, member.id)

    assert not avatar.exists()
    assert not photo.exists()


async def test_exclude_member_without_account_just_removes_member(session, world):
    member = Member(region_id=world["moscow"].id, full_name="Без Кабинета")
    session.add(member)
    await session.commit()
    member_id = member.id

    await exclude_member(session, member_id)

    session.expire_all()
    assert await session.get(Member, member_id) is None


async def test_exclude_member_rejects_unknown_id(session, world):
    with pytest.raises(ValueError):
        await exclude_member(session, 999999)


async def test_exclude_member_with_account_cleans_up_every_reference(session, world):
    member = Member(region_id=world["moscow"].id, full_name="С Кабинетом")
    session.add(member)
    await session.flush()

    user = User(full_name=member.full_name, role=ROLE_PARTICIPANT, member_id=member.id, telegram_id=555001)
    session.add(user)
    await session.flush()

    other = world["federal"]

    # Nullable FK — должны быть обнулены, а не мешать удалению.
    tx = Transaction(region_id=world["moscow"].id, author_id=user.id, amount=100, type="expense", date=date(2026, 1, 1))
    doc = Document(region_id=world["moscow"].id, title="Файл", stored_path="x", original_name="x.txt", author_id=user.id)
    event_row = Event(region_id=world["moscow"].id, title="Событие", date=date(2026, 1, 1), responsible_member_id=member.id)
    application = MembershipApplication(
        region_id=world["moscow"].id, telegram_id=1, full_name="Кто-то", reviewed_by_user_id=user.id
    )
    session.add_all([tx, doc, event_row, application])

    # NOT NULL — должны быть удалены целиком, оставить не с кем.
    task = Task(from_user_id=other.id, to_user_id=user.id, title="Задача")
    # Исключаемый — автор новости; NewsPost.author_user_id NOT NULL, как у
    # задачи выше: это тот самый случай, который однажды уронил exclude_member
    # на проде (тогда — на авторе рассылки, которую новости и заменили).
    own_post = NewsPost(author_user_id=user.id, text="Своя новость", byline="Академисты | Москва")
    other_post = NewsPost(author_user_id=other.id, text="Чужая новость", byline="Братство Академистов")
    session.add_all([task, own_post, other_post])
    await session.flush()
    notice = BirthdayNotice(member_id=member.id, year=2026)
    session.add(notice)

    # Руководство регионом/ячейкой, координация региона — снимается, не блокирует.
    region = Region(name="Регион для исключения теста", leader_user_id=user.id)
    session.add(region)
    await session.flush()
    cell = UniversityCell(region_id=world["moscow"].id, name="Тестовая ячейка для исключения", leader_user_id=user.id)
    coord_link = CoordinatorRegion(coordinator_user_id=user.id, region_id=world["tula"].id)
    session.add_all([cell, coord_link])

    # След под чужой новостью: просмотр, отклик, комментарий. Именно на нём
    # исключение однажды и упало пятисотой — news_views держал учётную запись,
    # а перечень ссылок про эту таблицу не знал.
    quest = Quest(title="Прочитать книгу", emoji="📖")
    session.add(quest)
    await session.flush()
    event_task = EventTask(event_id=event_row.id, title="Принести стулья", due_date=date(2026, 1, 1))
    session.add(event_task)
    await session.flush()
    session.add_all([
        NewsView(post_id=other_post.id, user_id=user.id),
        NewsReaction(post_id=other_post.id, user_id=user.id, emoji="❤️"),
        NewsComment(post_id=other_post.id, author_user_id=user.id, text="Согласен"),
        BureauMember(user_id=user.id, title="Секретарь"),
        MemberQuestProgress(member_id=member.id, quest_id=quest.id),
        ShopPurchase(member_id=member.id, item_id="chevron", kind="physical", price_stars=10),
        EventAttendance(event_id=event_row.id, member_id=member.id),
        EventTaskAssignee(task_id=event_task.id, member_id=member.id),
    ])

    # Кто-то смотрит систему его глазами — тоже держало удаление.
    other.view_as_user_id = user.id
    await session.commit()

    user_id = user.id
    member_id = member.id
    region_id = region.id
    cell_id = cell.id
    task_id = task.id
    tx_id, doc_id, event_id, application_id = tx.id, doc.id, event_row.id, application.id
    own_post_id, other_post_id = own_post.id, other_post.id
    # Забираем до исключения: после expire_all обращение к полю потянуло бы
    # запрос из синхронного контекста.
    other_id = other.id

    await exclude_member(session, member_id)
    session.expire_all()

    assert await session.get(Member, member_id) is None
    assert await session.get(User, user_id) is None

    tx_after = await session.get(Transaction, tx_id)
    assert tx_after.author_id is None
    doc_after = await session.get(Document, doc_id)
    assert doc_after.author_id is None
    event_after = await session.get(Event, event_id)
    assert event_after.responsible_member_id is None
    application_after = await session.get(MembershipApplication, application_id)
    assert application_after.reviewed_by_user_id is None

    assert await session.get(Task, task_id) is None
    # Своя новость уходит вместе с автором.
    assert await session.get(NewsPost, own_post_id) is None
    # Чужая — остаётся: исключаемый к ней отношения не имеет.
    assert await session.get(NewsPost, other_post_id) is not None
    assert (
        await session.execute(select(BirthdayNotice).where(BirthdayNotice.member_id == member_id))
    ).scalars().all() == []
    assert (
        await session.execute(select(CoordinatorRegion).where(CoordinatorRegion.coordinator_user_id == user_id))
    ).scalars().all() == []

    region_after = await session.get(Region, region_id)
    assert region_after.leader_user_id is None
    cell_after = await session.get(UniversityCell, cell_id)
    assert cell_after.leader_user_id is None

    # Ни одна ссылка не пережила исключение.
    for model, column in (
        (NewsView, NewsView.user_id),
        (NewsReaction, NewsReaction.user_id),
        (NewsComment, NewsComment.author_user_id),
        (BureauMember, BureauMember.user_id),
    ):
        assert (await session.execute(select(model).where(column == user_id))).scalars().all() == []
    for model, column in (
        (MemberQuestProgress, MemberQuestProgress.member_id),
        (ShopPurchase, ShopPurchase.member_id),
        (EventAttendance, EventAttendance.member_id),
        (EventTaskAssignee, EventTaskAssignee.member_id),
    ):
        assert (await session.execute(select(model).where(column == member_id))).scalars().all() == []

    assert (await session.get(User, other_id)).view_as_user_id is None


async def test_delete_member_endpoint_is_hard_delete_not_soft(client, session, world):
    login(world["leader_moscow"])
    member = Member(region_id=world["moscow"].id, full_name="Через API")
    session.add(member)
    await session.commit()
    member_id = member.id

    response = await client.delete(f"/api/members/{member_id}")
    assert response.status_code == 200

    session.expire_all()
    assert await session.get(Member, member_id) is None


async def test_leader_cannot_exclude_member_of_other_region(client, session, world):
    member = Member(region_id=world["tula"].id, full_name="Чужой Регион")
    session.add(member)
    await session.commit()
    member_id = member.id

    login(world["leader_moscow"])
    response = await client.delete(f"/api/members/{member_id}")
    assert response.status_code == 403

    session.expire_all()
    assert await session.get(Member, member_id) is not None
