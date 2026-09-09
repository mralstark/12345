"""Новости: общая лента Братства.

Публиковать может любой; разделения по уровням доступа нет — новость видят
все, кто открыл кабинет. От чьего имени сказано, показывает подпись, которую
считает byline_for и которая сохраняется в посте в момент публикации.
"""

import asyncio
import hashlib
import hmac
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import BOT_TOKEN, MAX_UPLOAD_BYTES, STORAGE_DIR
from database.models import (
    NEWS_REACTIONS,
    ROLE_CELL_LEADER,
    ROLE_COORDINATOR,
    ROLE_FEDERAL,
    ROLE_LEADER,
    ROLE_SUPERUSER,
    NewsComment,
    NewsNotification,
    NewsPhoto,
    NewsPost,
    NewsReaction,
    NewsView,
    Member,
    Region,
    User,
)
from services.images import AVATAR_MAX_SIDE, PHOTO_MAX_SIDE, shrink, suffix_for
from services.moderation import check as check_text
from utils.access import AccessDenied, accessible_region_ids, actor_cell


# За сколько секунд повтор того же текста считается вторым нажатием, а не
# второй новостью. С запасом: наблюдавшийся случай уложился в 32 секунды.
DUPLICATE_WINDOW_SECONDS = 120

# Комментарии закрыты. Лента стала каналом объявлений движения — в ней
# говорит ячейка, отделение или Братство, — а обсуждение под объявлением
# превращало её обратно в стену. Разговор у Братства и так есть, в чатах;
# приложение нужно для другого. Написанное раньше из базы не стирали: если
# решим вернуть, всё на месте.
COMMENTS_OPEN = False
COMMENTS_CLOSED_HINT = "Комментарии отключены"


def can_post_news(user: User) -> bool:
    """Пишет в ленту только руководитель — и только от лица ячейки, отделения
    или Братства (can_post_officially, official_byline).

    Раньше писал любой, а роль решала лишь подпись. От этого лента получалась
    разговором: рядом с объявлением отделения стояла личная запись участника,
    и читателю приходилось каждый раз разбирать, кто перед ним — организация
    или человек. Лента задумана как канал движения, а не как стена: в ней
    говорит Братство, а не отдельные его члены.
    """
    return can_post_officially(user)


def short_name(full_name: str) -> str:
    """Фамилия и имя без отчества: в ленте отчество только удлиняет подпись.
    ФИО хранится одной строкой (Member.full_name), поэтому берём два слова."""
    parts = (full_name or "").split()
    return " ".join(parts[:2]) if parts else ""


def can_post_officially(user: User) -> bool:
    """От имени отделения или Братства говорят только руководители."""
    return user.role in (
        ROLE_SUPERUSER,
        ROLE_FEDERAL,
        ROLE_COORDINATOR,
        ROLE_LEADER,
        ROLE_CELL_LEADER,
    )


async def official_byline(session: AsyncSession, author: User) -> str:
    """Официальная подпись по роли: от Братства или от своего отделения."""
    if author.role in (ROLE_SUPERUSER, ROLE_FEDERAL, ROLE_COORDINATOR):
        return "Братство Академистов"

    if author.role == ROLE_CELL_LEADER:
        cell = await actor_cell(session, author)
        return f"Академисты | {cell.name}" if cell is not None else "Академисты"

    if author.role == ROLE_LEADER:
        region_ids = await accessible_region_ids(session, author)
        region = await session.get(Region, region_ids[0]) if region_ids else None
        return f"Академисты | {region.name}" if region is not None else "Академисты"

    return short_name(author.full_name)


BYLINE_PERSONAL = "personal"
BYLINE_BRATSTVO = "bratstvo"
BYLINE_OTDELENIE = "otdelenie"

# Логотипы официальных подписей: у Братства свой, у отделений и ячеек общий.
BYLINE_LOGOS = {
    BYLINE_BRATSTVO: "/static/logo-bratstvo.jpg",
    BYLINE_OTDELENIE: "/static/logo-otdelenie.jpg",
}


def byline_kind_for(author: User, official: bool) -> str:
    """Чем подписан пост. От вида зависит аватар и то, открывается ли профиль:
    у поста от Братства или отделения за подписью нет человека."""
    if not (official and can_post_officially(author)):
        return BYLINE_PERSONAL
    if author.role in (ROLE_SUPERUSER, ROLE_FEDERAL, ROLE_COORDINATOR):
        return BYLINE_BRATSTVO
    return BYLINE_OTDELENIE


async def byline_for(session: AsyncSession, author: User, official: bool = False) -> str:
    """От чьего имени выходит пост.

    Решает не роль сама по себе, а кабинет, из которого пишут: из личного
    руководитель говорит от себя, из кабинета управления — от отделения или
    от Братства. Один и тот же человек в ленте выступает в двух ролях, и
    читателю должно быть видно, в какой именно.
    """
    if official and can_post_officially(author):
        return await official_byline(session, author)
    return short_name(author.full_name)


async def create_news(
    session: AsyncSession, author: User, text: str, official: bool = False
) -> NewsPost:
    text = text.strip()
    if not text:
        raise ValueError("Пустой текст новости")
    # Право писать проверяем здесь, а не только в роутере: кнопку в приложении
    # спрятать мало — запрос уходит и мимо неё.
    if not can_post_news(author):
        raise AccessDenied("В ленту пишет руководитель — от лица ячейки, отделения или Братства")
    if official and not can_post_officially(author):
        raise AccessDenied("От имени отделения публикует только руководитель")

    # Мат до ленты не доходит вовсе, спорное выходит с уведомлением
    # руководству — решение о нём принимает вызывающий (api/routers/news.py).
    flagged = await check_text(session, text)

    # Повтор того же текста от того же человека за считаные секунды — это не
    # вторая новость, а второе нажатие. Однажды так вышло двенадцать
    # одинаковых постов за полминуты: пока запрос шёл, кнопка оставалась
    # живой, и человек жал её снова. Кнопку мы починили, но полагаться только
    # на неё нельзя — сорвавшаяся сеть повторит запрос сама, и никакой
    # интерфейс этому не помешает.
    recent = (
        await session.execute(
            select(NewsPost).where(
                NewsPost.author_user_id == author.id,
                NewsPost.text == text,
                # Именно всемирное время: created_at пишет база (func.now()),
                # а она в UTC. Со временем машины окно уезжало бы на часовой
                # пояс — и защита не срабатывала бы вовсе.
                NewsPost.created_at
                >= datetime.now(timezone.utc).replace(tzinfo=None)
                - timedelta(seconds=DUPLICATE_WINDOW_SECONDS),
            ).order_by(NewsPost.created_at.desc())
        )
    ).scalars().first()
    if recent is not None:
        recent.flagged_words = []
        return recent

    post = NewsPost(
        author_user_id=author.id,
        text=text,
        byline=await byline_for(session, author, official),
        byline_kind=byline_kind_for(author, official),
    )
    session.add(post)
    await session.commit()
    await session.refresh(post)
    # Что насторожило — кладём на сам объект: заводить ради этого поле в базе
    # незачем, значение нужно ровно один раз, сразу после публикации.
    post.flagged_words = flagged
    return post


# --- Кому уходит уведомление в бот ------------------------------------------
# Круг адресатов — ровно тот, что читатель видит в подписи поста, и это не
# случайность: подпись «Братство Академистов» обещает, что говорят от имени
# всего Братства, «Академисты | Москва» — что от имени отделения. Уведомление
# идёт по тому же кругу, иначе подпись обещала бы одно, а рассылка делала
# другое. Личные посты не уведомляют никого: лента открыта всем, и рассылка с
# каждого личного поста была бы не новостью, а спамом.

async def news_audience_telegram_ids(session: AsyncSession, post: NewsPost) -> list[int]:
    """Telegram-id тех, кому уходит уведомление об этом посте. Автора среди
    них нет — он и так знает, что опубликовал."""
    if post.byline_kind == BYLINE_PERSONAL:
        return []

    author = await session.get(User, post.author_user_id)
    if author is None:
        return []

    query = select(User.telegram_id).where(
        User.is_active.is_(True),
        User.telegram_id.is_not(None),
        User.id != post.author_user_id,
    )

    if post.byline_kind == BYLINE_OTDELENIE:
        # Вузовское отделение — своя ячейка, городское — свой регион: то же
        # деление, по которому official_byline составляет саму подпись.
        query = query.join(Member, Member.id == User.member_id)
        cell = await actor_cell(session, author)
        if cell is not None:
            query = query.where(Member.cell_id == cell.id)
        else:
            region_ids = await accessible_region_ids(session, author)
            if not region_ids:
                return []
            query = query.where(Member.region_id.in_(region_ids))

    return list((await session.execute(query)).scalars().all())


async def news_feed(session: AsyncSession, user: User, limit: int = 50) -> list[NewsPost]:
    """Общая лента: видят все и всё. Разделения по отделениям нет."""
    query = (
        select(NewsPost)
        .order_by(NewsPost.created_at.desc(), NewsPost.id.desc())
        .limit(limit)
    )
    return list((await session.execute(query)).scalars().all())


async def can_see_post(session: AsyncSession, user: User, post: NewsPost) -> bool:
    """Оставлено одной точкой, через которую проходят фотографии, комментарии,
    реакции и просмотры: сейчас лента общая и ответ всегда положительный, но
    если ограничения вернутся, править нужно будет только здесь."""
    return True


# Уровни для модерации. Удалять чужие посты можно только строго ниже себя и
# только в своих границах: иначе руководитель отделения смог бы снести
# федеральное объявление, оказавшееся «в его регионе» по автору.
ROLE_LEVELS = {
    ROLE_SUPERUSER: 4,
    ROLE_FEDERAL: 4,
    ROLE_COORDINATOR: 3,
    ROLE_LEADER: 2,
    ROLE_CELL_LEADER: 1,
}


async def _can_moderate(session: AsyncSession, user: User, author: User) -> bool:
    """Может ли этот руководитель убирать написанное этим автором.

    Условий два: автор строго ниже уровнем и находится в границах модератора —
    координатор по своим регионам, руководитель отделения по своему региону,
    руководитель ячейки по своей ячейке. Без проверки уровня руководитель
    отделения смог бы снести федеральное объявление, чей автор формально
    числится в его регионе.

    Уровень автора берётся текущий, а не на момент публикации: понизили
    человека — его прежние посты становятся доступны модерации отделения.
    """
    my_level = ROLE_LEVELS.get(user.role, 0)
    if my_level >= 4:
        return True
    if my_level == 0 or my_level <= ROLE_LEVELS.get(author.role, 0):
        return False

    member = await session.get(Member, author.member_id) if author.member_id else None
    if member is None:
        return False

    if user.role == ROLE_CELL_LEADER:
        cell = await actor_cell(session, user)
        return cell is not None and member.cell_id == cell.id

    return member.region_id in set(await accessible_region_ids(session, user))


async def can_delete_post(
    session: AsyncSession, user: User, post: NewsPost, *, personal: bool = False
) -> bool:
    """Свой пост — всегда; чужой — по правилу уровней (_can_moderate).

    personal=True — человек смотрит ленту из личного кабинета. Там он такой же
    читатель, как все, и права руководителя не при нём: чужое убирают из
    кабинета управления, осознанно, а не мимоходом со своей ленты.
    """
    if post.author_user_id == user.id:
        return True
    if personal:
        return False
    author = await session.get(User, post.author_user_id)
    if author is None:
        return ROLE_LEVELS.get(user.role, 0) >= 4
    return await _can_moderate(session, user, author)


async def delete_news(session: AsyncSession, user: User, post_id: int, *, personal: bool = False) -> dict:
    """Удаляет новость и возвращает то, что нужно для уборки следов: подпись
    поста и квитанции об уведомлениях.

    Квитанции забираем ДО удаления: каскад унесёт их вместе с постом, а
    переписать сообщения в боте надо уже после — и знать, какие именно.
    """
    post = await session.get(NewsPost, post_id)
    if post is None:
        raise ValueError("Новость не найдена")
    if not await can_delete_post(session, user, post, personal=personal):
        raise AccessDenied(
            "Свою новость убирает автор, чужую — руководитель уровнем выше, "
            "и делает это из кабинета управления"
            if personal
            else "Удалить эту новость может её автор или руководитель уровнем выше"
        )

    sent = (
        await session.execute(select(NewsNotification).where(NewsNotification.post_id == post.id))
    ).scalars().all()
    trace = {
        "byline": post.byline,
        "messages": [(row.chat_id, row.message_id) for row in sent],
    }

    # Каскад заберёт строки фотографий, комментариев, реакций и просмотров, но
    # файлы на диске надо убрать руками — иначе STORAGE_DIR растёт мусором.
    photos = (
        await session.execute(select(NewsPhoto).where(NewsPhoto.post_id == post.id))
    ).scalars().all()
    for photo in photos:
        target = (STORAGE_DIR / photo.stored_path).resolve()
        if target.is_file() and STORAGE_DIR.resolve() in target.parents:
            target.unlink()

    await session.delete(post)
    await session.commit()
    return trace





# Картинки, которые браузер покажет без скачивания. Всё остальное — не фото,
# и класть его в галерею незачем.
ALLOWED_PHOTO_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif", "image/heic")

# Каталог фотографий внутри STORAGE_DIR — отдельно от документов регионов,
# чтобы удаление новости не бродило по чужим папкам.
PHOTO_DIR = "news"

# Сколько фотографий можно приложить к одной новости. Предела не было вовсе:
# альбом в ленте всё равно показывает первые четыре, а остальные прячет под
# «ещё», так что сотня снимков в посте не помогла бы никому — только заняла бы
# место и время на загрузку.
PHOTO_LIMIT = 10


def _safe_suffix(filename: str) -> str:
    suffix = Path(filename or "").suffix[:16]
    return suffix if re.fullmatch(r"\.[A-Za-z0-9]{1,15}", suffix or "") else ""


async def attach_photos(session: AsyncSession, post: NewsPost, files: list) -> int:
    """Сохраняет файлы на диск и привязывает их к новости. Возвращает число
    добавленных. Порядок в галерее — порядок, в котором их прислали."""
    added = 0
    incoming = [f for f in (files or []) if f is not None]
    if len(incoming) > PHOTO_LIMIT:
        raise ValueError(f"К одной новости можно приложить не больше {PHOTO_LIMIT} фотографий")

    for index, upload in enumerate(incoming):
        payload = await upload.read()
        if not payload:
            continue
        if len(payload) > MAX_UPLOAD_BYTES:
            raise ValueError(f"Фото больше допустимых {MAX_UPLOAD_BYTES // (1024 * 1024)} МБ")
        if upload.content_type not in ALLOWED_PHOTO_TYPES:
            raise ValueError("Можно прикреплять только изображения")

        # Предел выше проверен по исходному размеру — уменьшаем уже после,
        # чтобы гигантский файл не разворачивался в память ради того, чтобы
        # быть отвергнутым.
        #
        # В отдельном потоке, а не здесь же: сжатие снимка с телефона занимает
        # около 0,4 секунды (замерено на сервере), и всё это время обработчик
        # не отдавал управление — кабинет замирал для всех, кто в этот момент
        # им пользовался. Ядро на сервере одно, подхватить некому. Теперь
        # цикл событий свободен и обслуживает остальных, пока идёт сжатие.
        payload, content_type = await asyncio.to_thread(shrink, payload, PHOTO_MAX_SIDE)
        content_type = content_type or upload.content_type
        suffix = suffix_for(content_type) or _safe_suffix(upload.filename)

        stored_name = f"{uuid.uuid4().hex}{suffix}"
        relative = Path(PHOTO_DIR) / str(post.id) / stored_name
        target = STORAGE_DIR / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)

        session.add(
            NewsPhoto(
                post_id=post.id,
                stored_path=relative.as_posix(),
                original_name=(upload.filename or stored_name)[:255],
                content_type=content_type,
                size_bytes=len(payload),
                sort_order=index,
            )
        )
        added += 1
    if added:
        await session.commit()
    return added



async def add_comment(session: AsyncSession, user: User, post_id: int, text: str) -> NewsComment:
    text = text.strip()
    if not text:
        raise ValueError("Пустой комментарий")
    await check_text(session, text)
    post = await session.get(NewsPost, post_id)
    if post is None:
        raise ValueError("Новость не найдена")
    if not await can_see_post(session, user, post):
        raise AccessDenied("Новость недоступна")

    comment = NewsComment(post_id=post_id, author_user_id=user.id, text=text)
    session.add(comment)
    await session.commit()
    await session.refresh(comment)
    return comment


async def can_delete_comment(
    session: AsyncSession, user: User, comment: NewsComment, *, personal: bool = False
) -> bool:
    """Свой комментарий и комментарий под своим постом — всегда; в остальном
    то же правило уровней, что и для постов (can_delete_post).

    Под своим постом убирает и из личного кабинета: это его пост, ему за
    порядком под ним и следить. personal закрывает только чужое под чужим.
    """
    if comment.author_user_id == user.id:
        return True
    post = await session.get(NewsPost, comment.post_id)
    if post is not None and post.author_user_id == user.id:
        return True
    if personal:
        return False

    author = await session.get(User, comment.author_user_id)
    if author is None:
        return False
    return await _can_moderate(session, user, author)


async def delete_comment(
    session: AsyncSession, user: User, comment_id: int, *, personal: bool = False
) -> None:
    comment = await session.get(NewsComment, comment_id)
    if comment is None:
        raise ValueError("Комментарий не найден")
    if not await can_delete_comment(session, user, comment, personal=personal):
        raise AccessDenied("Удалить комментарий может автор, автор новости или федеральный")
    await session.delete(comment)
    await session.commit()


# --- Подписанные ссылки на фотографии ---------------------------------------
# Картинку в ленте показывает тег <img>, а его запрос браузер отправляет сам —
# заголовок Authorization из api() к нему не прикрепится, и обычная проверка
# сессии отвечала бы 401 (именно так фотографии и не грузились). Поэтому
# ссылку подписываем: выдаёт её лента, которая уже проверила право на просмотр,
# а сам файл отдаётся по действительной подписи.
#
# Плата за это: пока подпись жива, файл откроет любой, у кого есть ссылка.
# Отсюда короткий срок жизни и привязка подписи к конкретной фотографии.
PHOTO_URL_TTL_SECONDS = 6 * 60 * 60

# Срок годности в подписи считается не от текущей секунды, а от начала часа.
# Иначе каждая отрисовка ленты давала фотографии новый адрес, а браузер хранит
# кэш по адресу — и каждое открытие ленты качало все фотографии заново, хотя
# Cache-Control на них стоит. Теперь в течение часа адрес один и тот же и кэш
# наконец срабатывает. Плата — подпись живёт от ttl до ttl+час вместо ровно
# ttl; для ссылки на картинку это безразлично.
TOKEN_BUCKET_SECONDS = 60 * 60


def _token_expiry(ttl: int) -> int:
    now = int(time.time())
    return (now // TOKEN_BUCKET_SECONDS) * TOKEN_BUCKET_SECONDS + TOKEN_BUCKET_SECONDS + ttl


def _photo_secret() -> bytes:
    return hmac.new(b"news-photo", (BOT_TOKEN or "dev").encode(), hashlib.sha256).digest()


def photo_token(photo_id: int, ttl: int = PHOTO_URL_TTL_SECONDS) -> str:
    expires = _token_expiry(ttl)
    signature = hmac.new(
        _photo_secret(), f"{photo_id}:{expires}".encode(), hashlib.sha256
    ).hexdigest()[:32]
    return f"{expires}.{signature}"


def photo_token_valid(photo_id: int, token: str) -> bool:
    expires_raw, _, signature = (token or "").partition(".")
    if not signature or not expires_raw.isdigit():
        return False
    if int(expires_raw) < time.time():
        return False
    expected = hmac.new(
        _photo_secret(), f"{photo_id}:{expires_raw}".encode(), hashlib.sha256
    ).hexdigest()[:32]
    return hmac.compare_digest(expected, signature)


def photo_url(photo_id: int) -> str:
    return f"/api/news/photos/{photo_id}?t={photo_token(photo_id)}"


# --- Реакции и просмотры -----------------------------------------------------

async def toggle_reaction(session: AsyncSession, user: User, post_id: int, emoji: str) -> None:
    """Одна реакция на человека: та же снимается, другая заменяет прежнюю."""
    if emoji not in NEWS_REACTIONS:
        raise ValueError("Такой реакции нет")
    post = await session.get(NewsPost, post_id)
    if post is None:
        raise ValueError("Новость не найдена")
    if not await can_see_post(session, user, post):
        raise AccessDenied("Новость недоступна")

    existing = (
        await session.execute(
            select(NewsReaction).where(
                NewsReaction.post_id == post_id, NewsReaction.user_id == user.id
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        session.add(NewsReaction(post_id=post_id, user_id=user.id, emoji=emoji))
    elif existing.emoji == emoji:
        await session.delete(existing)
    else:
        existing.emoji = emoji
    await session.commit()


async def reactions_for(session: AsyncSession, post_id: int, user: User) -> list[dict]:
    """Сводка по реакциям поста в порядке NEWS_REACTIONS, без нулевых."""
    rows = (
        await session.execute(select(NewsReaction).where(NewsReaction.post_id == post_id))
    ).scalars().all()
    counts: dict[str, int] = {}
    mine = None
    for row in rows:
        counts[row.emoji] = counts.get(row.emoji, 0) + 1
        if row.user_id == user.id:
            mine = row.emoji
    return [
        {"emoji": emoji, "count": counts[emoji], "mine": emoji == mine}
        for emoji in NEWS_REACTIONS
        if counts.get(emoji)
    ]


async def mark_viewed(session: AsyncSession, user: User, post_ids: list[int]) -> None:
    """Отмечает просмотры пачкой — лента сообщает о постах, доехавших до экрана.
    Считаем уникальных читателей, поэтому повторные отметки пропускаем."""
    if not post_ids:
        return
    seen = set(
        (
            await session.execute(
                select(NewsView.post_id).where(
                    NewsView.user_id == user.id, NewsView.post_id.in_(post_ids)
                )
            )
        ).scalars().all()
    )
    added = False
    for post_id in set(post_ids) - seen:
        post = await session.get(NewsPost, post_id)
        if post is None or not await can_see_post(session, user, post):
            continue
        session.add(NewsView(post_id=post_id, user_id=user.id))
        added = True
    if added:
        await session.commit()


async def views_count(session: AsyncSession, post_id: int) -> int:
    from sqlalchemy import func as sa_func

    return (
        await session.execute(
            select(sa_func.count(NewsView.id)).where(NewsView.post_id == post_id)
        )
    ).scalar() or 0


# --- Аватары ------------------------------------------------------------------

ALLOWED_AVATAR_TYPES = ("image/jpeg", "image/png", "image/webp")
AVATAR_DIR = "avatars"


async def save_avatar(session: AsyncSession, member: Member, upload) -> str:
    """Свой круглый аватар. Старый файл удаляем: иначе каждая замена оставляет
    мусор в STORAGE_DIR."""
    payload = await upload.read()
    if not payload:
        raise ValueError("Пустой файл")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise ValueError(f"Файл больше допустимых {MAX_UPLOAD_BYTES // (1024 * 1024)} МБ")
    if upload.content_type not in ALLOWED_AVATAR_TYPES:
        raise ValueError("Аватар должен быть изображением")

    # В отдельном потоке — по той же причине, что и фотографии в attach_photos.
    payload, content_type = await asyncio.to_thread(shrink, payload, AVATAR_MAX_SIDE)
    suffix = suffix_for(content_type) or _safe_suffix(upload.filename)

    previous = member.avatar_path
    stored_name = f"{uuid.uuid4().hex}{suffix}"
    relative = Path(AVATAR_DIR) / str(member.id) / stored_name
    target = STORAGE_DIR / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)

    member.avatar_path = relative.as_posix()
    await session.commit()

    if previous:
        old = (STORAGE_DIR / previous).resolve()
        if old.is_file() and STORAGE_DIR.resolve() in old.parents:
            old.unlink()
    return member.avatar_path


def avatar_token(member_id: int, ttl: int = PHOTO_URL_TTL_SECONDS) -> str:
    expires = _token_expiry(ttl)
    signature = hmac.new(
        _photo_secret(), f"avatar:{member_id}:{expires}".encode(), hashlib.sha256
    ).hexdigest()[:32]
    return f"{expires}.{signature}"


def avatar_token_valid(member_id: int, token: str) -> bool:
    expires_raw, _, signature = (token or "").partition(".")
    if not signature or not expires_raw.isdigit() or int(expires_raw) < time.time():
        return False
    expected = hmac.new(
        _photo_secret(), f"avatar:{member_id}:{expires_raw}".encode(), hashlib.sha256
    ).hexdigest()[:32]
    return hmac.compare_digest(expected, signature)


async def avatar_url_for_user(session: AsyncSession, user: User | None) -> str | None:
    """Свой загруженный аватар, иначе образ из Академии, иначе ничего."""
    if user is None or user.member_id is None:
        return None
    member = await session.get(Member, user.member_id)
    if member is None:
        return None
    if member.avatar_path:
        return f"/api/profile/avatar/{member.id}?t={avatar_token(member.id)}"
    if member.avatar_outfit:
        return f"/static/avatars/{member.avatar_outfit}.png"
    return None
