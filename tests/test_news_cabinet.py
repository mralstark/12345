"""Откуда можно убирать чужие посты и в каком поясе показывается время.

Права руководителя никуда не деваются — меняется место, где они при нём.
Личный кабинет — это его собственная лента, там он такой же читатель, как
все; чужое убирают из кабинета управления, куда заходят именно за этим.
Иначе крестик стоит под каждым постом всегда, и однажды им промахиваются.
"""

from datetime import datetime

from database.models import ROLE_CELL_LEADER, ROLE_PARTICIPANT, Member, UniversityCell, User
from services.news import create_news
from tests.conftest import login
from utils.tz import iso_utc


async def _participant(session, region_id, name, telegram_id):
    member = Member(region_id=region_id, full_name=name)
    session.add(member)
    await session.flush()
    user = User(full_name=name, role=ROLE_PARTICIPANT, member_id=member.id, telegram_id=telegram_id)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def _cell_leader(session, region_id, cell_name, name, telegram_id):
    """Автор поста в ленте — только руководитель: участник туда не пишет."""
    member = Member(region_id=region_id, full_name=name)
    session.add(member)
    await session.flush()
    user = User(full_name=name, role=ROLE_CELL_LEADER, member_id=member.id, telegram_id=telegram_id)
    session.add(user)
    await session.flush()
    cell = UniversityCell(region_id=region_id, name=cell_name, leader_user_id=user.id)
    session.add(cell)
    await session.flush()
    member.cell_id = cell.id
    await session.commit()
    await session.refresh(user)
    return user


# --- Чужой пост ---------------------------------------------------------------


async def test_leader_cannot_delete_a_stranger_from_personal_cabinet(client, world, session):
    author = await _cell_leader(session, world["moscow"].id, "МГУ", "Сидоров Пётр", 4001)
    post = await create_news(session, author, "Собрание в четверг")

    login(world["leader_moscow"])
    response = await client.delete(f"/api/news/{post.id}?personal=true")

    assert response.status_code == 403
    assert (await client.get("/api/news")).json()["items"], "пост исчез, хотя удалять было нельзя"


async def test_the_same_leader_deletes_it_from_the_management_cabinet(client, world, session):
    """Права никуда не делись — важно только, откуда ими пользуются."""
    author = await _cell_leader(session, world["moscow"].id, "МГТУ", "Сидоров Пётр", 4002)
    post = await create_news(session, author, "Собрание в четверг")

    login(world["leader_moscow"])
    assert (await client.delete(f"/api/news/{post.id}")).status_code == 200


async def test_own_post_is_deletable_from_the_personal_cabinet(client, world, session):
    """Своё убирают откуда угодно: это своё."""
    login(world["leader_moscow"])
    post_id = (await client.post("/api/news", data={"text": "Моя новость"})).json()["id"]

    assert (await client.delete(f"/api/news/{post_id}?personal=true")).status_code == 200


async def test_the_cross_is_not_even_shown_in_the_personal_cabinet(client, world, session):
    """Кнопки быть не должно — отказ на нажатие это уже вторая линия."""
    author = await _cell_leader(session, world["moscow"].id, "МИФИ", "Сидоров Пётр", 4003)
    await create_news(session, author, "Собрание в четверг")
    login(world["leader_moscow"])

    personal = (await client.get("/api/news?personal=true")).json()["items"][0]
    management = (await client.get("/api/news")).json()["items"][0]

    assert personal["can_delete"] is False
    assert management["can_delete"] is True


async def test_old_comment_under_own_post_stays_deletable(client, world, session):
    """Новых комментариев не бывает, но написанное раньше осталось в базе, и
    убрать его под своим постом человек может по-прежнему — в том числе из
    личного кабинета: под своим он хозяин везде."""
    from services.news import add_comment

    login(world["leader_moscow"])
    post_id = (await client.post("/api/news", data={"text": "Собрание"})).json()["id"]

    guest = await _participant(session, world["moscow"].id, "Гость Гостев", 4004)
    comment = await add_comment(session, guest, post_id, "Приду")

    login(world["leader_moscow"])
    assert (await client.delete(f"/api/news/comments/{comment.id}?personal=true")).status_code == 200


# --- Откуда пишут -------------------------------------------------------------


async def test_nobody_posts_from_the_personal_cabinet(client, world):
    """Из личного кабинета не пишет никто — даже тот, кто вправе писать.

    Лента — голос организации, и включают его из кабинета управления, куда
    заходят именно за этим. В личном человек читатель, кем бы он ни был.
    """
    login(world["leader_moscow"])

    personal = (await client.get("/api/news?personal=true")).json()
    assert personal["can_post"] is False

    refused = await client.post(
        "/api/news", data={"text": "Из личного", "official": "true", "personal": "true"}
    )
    assert refused.status_code == 403

    management = (await client.get("/api/news")).json()
    assert management["can_post"] is True
    assert (await client.post("/api/news", data={"text": "Из управления", "official": "true"})).status_code == 200


# --- Время --------------------------------------------------------------------


async def test_post_time_is_marked_as_utc(client, world):
    """Без пометки браузер читает время как местное: пост, выставленный в
    Москве в 17:06, показывался как 14:06."""
    login(world["leader_moscow"])
    await client.post("/api/news", data={"text": "Собрание"})

    created = (await client.get("/api/news")).json()["items"][0]["created_at"]

    assert created.endswith("+00:00"), f"время без пояса: {created!r}"


def test_iso_utc_leaves_the_moment_alone():
    """Пометка меняет подпись, а не сам момент: 14:06 UTC — это 17:06 в Москве,
    и переводить его должен тот, кто читает, а не сервер."""
    assert iso_utc(datetime(2026, 8, 26, 14, 6)) == "2026-08-26T14:06:00+00:00"
    assert iso_utc(None) is None
