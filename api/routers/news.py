"""Новости кабинета: общая лента и публикация.

Писать может любой, видят все. Роль автора решает не аудиторию, а подпись
под постом (services/news.py::byline_for).
"""

import logging
from html import escape

from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user, get_db
from config import STORAGE_DIR, WEBAPP_URL
from database.db import async_session
from database.models import (
    NEWS_REACTIONS,
    ROLE_FEDERAL,
    ROLE_SUPERUSER,
    NewsComment,
    NewsNotification,
    NewsPhoto,
    NewsPost,
    User,
)
from services.news import (
    COMMENTS_CLOSED_HINT,
    COMMENTS_OPEN,
    add_comment,
    attach_photos,
    byline_for,
    byline_kind_for,
    can_delete_comment,
    can_post_officially,
    can_delete_post,
    can_post_news,
    create_news,
    delete_comment,
    delete_news,
    mark_viewed,
    news_audience_telegram_ids,
    PHOTO_LIMIT,
    news_feed,
    photo_token_valid,
    photo_url,
    BYLINE_BRATSTVO,
    BYLINE_LOGOS,
    BYLINE_OTDELENIE,
    BYLINE_PERSONAL,
    avatar_url_for_user,
    reactions_for,
    short_name,
    toggle_reaction,
    views_count,
)
from utils.access import AccessDenied
from utils.notify import get_notifier_bot, notify_telegram
from utils.tz import iso_utc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/news", tags=["news"])


class CommentIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)



@router.get("")
async def list_news(
    # Из какого кабинета смотрят ленту. В личном человек — такой же читатель,
    # как все: крестик стоит только на своём (services/news.py::can_delete_post).
    personal: bool = False,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    posts = await news_feed(session, user)
    items = []
    for post in posts:
        author = await session.get(User, post.author_user_id)
        photos = (
            await session.execute(
                select(NewsPhoto).where(NewsPhoto.post_id == post.id).order_by(NewsPhoto.sort_order, NewsPhoto.id)
            )
        ).scalars().all()
        comments = (
            await session.execute(
                select(NewsComment).where(NewsComment.post_id == post.id).order_by(NewsComment.created_at, NewsComment.id)
            )
        ).scalars().all()

        comment_items = []
        for comment in comments:
            comment_author = await session.get(User, comment.author_user_id)
            comment_items.append(
                {
                    "id": comment.id,
                    "text": comment.text,
                    "author_user_id": comment.author_user_id,
                    "author_name": short_name(comment_author.full_name) if comment_author is not None else None,
                    "avatar": await avatar_url_for_user(session, comment_author),
                    "created_at": iso_utc(comment.created_at),
                    "can_delete": await can_delete_comment(session, user, comment, personal=personal),
                }
            )

        items.append(
            {
                "id": post.id,
                "text": post.text,
                "author_user_id": post.author_user_id,
                "author_name": short_name(author.full_name) if author is not None else None,
                "byline": post.byline or (short_name(author.full_name) if author is not None else ""),
                # У поста от Братства или отделения за подписью нет человека:
                # аватар — логотип, и профиль по нему не открывается.
                "byline_kind": post.byline_kind,
                "avatar": (
                    BYLINE_LOGOS.get(post.byline_kind)
                    if post.byline_kind != BYLINE_PERSONAL
                    else await avatar_url_for_user(session, author)
                ),
                "profile_open": post.byline_kind == BYLINE_PERSONAL,
                "created_at": iso_utc(post.created_at),
                "can_delete": await can_delete_post(session, user, post, personal=personal),
                "photos": [{"id": photo.id, "url": photo_url(photo.id)} for photo in photos],
                # Комментарии закрыты (services/news.py::COMMENTS_OPEN) —
                # ни новых, ни старых лента не показывает.
                "comments": comment_items if COMMENTS_OPEN else [],
                "reactions": await reactions_for(session, post.id, user),
                "views": await views_count(session, post.id),
            }
        )
    # Свой аватар и имя — для строки «Что нового?» над лентой.
    return {
        "items": items,
        # Из личного кабинета не пишет никто, кем бы ни был: лента — голос
        # организации, а личный кабинет — место, где человек читатель.
        # Кнопку прячем здесь, отказ на сам запрос — в publish ниже.
        "can_post": can_post_news(user) and not personal,
        "reaction_set": list(NEWS_REACTIONS),
        "me_avatar": await avatar_url_for_user(session, user),
        "me_name": short_name(user.full_name),
    }


@router.get("/byline")
async def my_byline(
    official: bool = False,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Подсказка для формы: как подпишется пост и кого потревожит уведомление.

    Кабинет, из которого пишут, решает — своим именем или от отделения
    (services/news.py::byline_for). Про уведомление форма раньше молчала, и
    человек не знал, что официальный пост разойдётся всем в бот.
    """
    kind = byline_kind_for(user, official)
    audience = {
        BYLINE_BRATSTVO: "Уведомление придёт всем",
        BYLINE_OTDELENIE: "Уведомление придёт вашему отделению",
    # У своего поста подписи нет: он никого не тревожит, и говорить об этом
    # незачем — строка сообщала бы об отсутствии события.
    }.get(kind)
    return {
        "byline": await byline_for(session, user, official),
        "byline_kind": kind,
        "avatar": BYLINE_LOGOS.get(kind) or await avatar_url_for_user(session, user),
        "audience": audience,
        "can_official": can_post_officially(user),
        "photo_limit": PHOTO_LIMIT,
    }



# Сколько текста поста уносить в уведомление. Целиком незачем: уведомление —
# приглашение открыть кабинет, а не замена ленте (и длинный пост всё равно
# упёрся бы в предел длины сообщения Telegram).
NOTIFICATION_TEXT_LIMIT = 350

# Знак Братства в уведомлении от Братства — кастомный эмодзи из набора
# academists. В Mini App такой по идентификатору не нарисовать, а в сообщении
# бота можно: Telegram подставит его сам.
BRATSTVO_EMOJI_ID = "5208770610980724566"


async def _send_news_notifications(post_id: int) -> None:
    """Фоновая задача: не задерживает ответ автору и может позволить себе
    терпеливые ретраи (разовый сбой DNS на сервере при обращении к
    api.telegram.org бывает длиннее пары секунд). Открывает свою сессию —
    сессия запроса к этому моменту уже закрыта.

    Адресатов считаем здесь, а не передаём списком: между публикацией и
    рассылкой список короткий, а тащить сотни id через границу задачи незачем.
    """
    async with async_session() as session:
        post = await session.get(NewsPost, post_id)
        if post is None:
            return
        telegram_ids = await news_audience_telegram_ids(session, post)
        byline, text, kind = post.byline, post.text, post.byline_kind

    if not telegram_ids:
        return

    body = text.strip()
    if len(body) > NOTIFICATION_TEXT_LIMIT:
        body = body[:NOTIFICATION_TEXT_LIMIT].rstrip() + "…"
    # Текст пишет человек, а уходит он с parse_mode="HTML" — без экранирования
    # любая угловая скобка в посте сломала бы отправку всем адресатам сразу.
    head = escape(byline)
    plain = f"📣 <b>{head}</b>\n\n{escape(body)}"

    # У Братства — свой знак вместо рупора. Кастомный эмодзи разрешён не
    # каждому боту, поэтому обычный вариант идёт запасным: отказ по
    # содержимому не должен оставить людей вовсе без уведомления.
    if kind == BYLINE_BRATSTVO:
        mark = f'<tg-emoji emoji-id="{BRATSTVO_EMOJI_ID}">📣</tg-emoji>'
        message = f"{mark} <b>{head}</b>\n\n{escape(body)}"
    else:
        message = plain

    keyboard = None
    if WEBAPP_URL:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🏛 Открыть в кабинете", web_app=WebAppInfo(url=WEBAPP_URL))]]
        )

    # Запоминаем, какое сообщение у кого получилось: без этого удалённая
    # новость продолжала бы висеть в боте — Telegram не знает наших постов,
    # ему нужен номер сообщения в конкретной переписке.
    receipts = []
    for telegram_id in telegram_ids:
        sent = await notify_telegram(
            telegram_id, message, reply_markup=keyboard,
            retries=4, retry_delay=5, fallback_text=plain,
        )
        if sent is not None:
            receipts.append(NewsNotification(post_id=post_id, chat_id=sent.chat.id, message_id=sent.message_id))

    if receipts:
        async with async_session() as session:
            session.add_all(receipts)
            await session.commit()


@router.post("")
async def publish(
    background_tasks: BackgroundTasks,
    text: str = Form(..., min_length=1, max_length=4000),
    official: bool = Form(default=False),
    # Из какого кабинета пишут. Из личного — ни от кого не принимаем: см.
    # can_post в list_news выше. Приходит частью той же формы, что текст и
    # фотографии, — запрос multipart, и query-параметр тут был бы вразнобой.
    personal: bool = Form(default=False),
    files: list[UploadFile] = File(default=[]),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Текст и фотографии приходят одним multipart-запросом: иначе при сбое
    на втором шаге осталась бы новость без картинок, которые автор приложил."""
    # Считаем фотографии до того, как новость появится в ленте: attach_photos
    # проверяет то же самое, но там пост уже записан, и отказ оставил бы его
    # висеть без картинок.
    if personal:
        raise AccessDenied("В ленту пишут из кабинета управления")

    if len([f for f in (files or []) if f is not None]) > PHOTO_LIMIT:
        raise ValueError(f"К одной новости можно приложить не больше {PHOTO_LIMIT} фотографий")

    post = await create_news(session, user, text, official=official)
    photos = await attach_photos(session, post, files)
    # Уведомление ставим в очередь после того, как фотографии легли на диск:
    # человек нажмёт кнопку в уведомлении и увидит пост целиком, а не текст
    # без картинок.
    background_tasks.add_task(_send_news_notifications, post.id)
    # Спорные слова пост не задерживают: он выходит, а руководство узнаёт, что
    # он вышел. Решать, нормально это или нет, — человеку, машина контекста не
    # понимает.
    flagged = getattr(post, "flagged_words", None)
    if flagged:
        background_tasks.add_task(
            _warn_leadership, post.id, post.byline, short_name(user.full_name), list(flagged)
        )
    return {"id": post.id, "byline": post.byline, "photos": photos}


@router.get("/photos/{photo_id}")
async def photo(
    photo_id: int,
    t: str = "",
    session: AsyncSession = Depends(get_db),
) -> FileResponse:
    """Отдаём по подписи из ссылки, а не по заголовку авторизации: запрос
    делает тег <img>, и заголовок из api() к нему не прикрепляется (см.
    services/news.py::photo_token). Право на просмотр проверено раньше — в
    момент, когда лента выдала подписанную ссылку."""
    if not photo_token_valid(photo_id, t):
        raise AccessDenied("Ссылка на фотографию недействительна или устарела")

    item = await session.get(NewsPhoto, photo_id)
    if item is None:
        raise HTTPException(404, "Фотография не найдена")

    path = (STORAGE_DIR / item.stored_path).resolve()
    if not path.is_file() or STORAGE_DIR.resolve() not in path.parents:
        raise HTTPException(404, "Файл не найден")
    return FileResponse(
        path,
        media_type=item.content_type or "image/jpeg",
        filename=item.original_name,
        headers={"Cache-Control": "private, max-age=900", "X-Content-Type-Options": "nosniff"},
    )


@router.post("/{post_id}/comments")
async def comment(
    post_id: int,
    payload: CommentIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    if not COMMENTS_OPEN:
        raise AccessDenied(COMMENTS_CLOSED_HINT)
    created = await add_comment(session, user, post_id, payload.text)
    return {"id": created.id}


@router.delete("/comments/{comment_id}")
async def remove_comment(
    comment_id: int,
    personal: bool = False,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    await delete_comment(session, user, comment_id, personal=personal)
    return {"ok": True}


class ReactionIn(BaseModel):
    emoji: str = Field(min_length=1, max_length=8)


class ViewsIn(BaseModel):
    post_ids: list[int] = Field(default_factory=list, max_length=200)


@router.post("/{post_id}/reactions")
async def react(
    post_id: int,
    payload: ReactionIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    await toggle_reaction(session, user, post_id, payload.emoji)
    return {"reactions": await reactions_for(session, post_id, user)}


@router.post("/views")
async def views(
    payload: ViewsIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Лента сообщает, какие посты доехали до экрана — считаем уникальных
    читателей. Пачкой, чтобы не бить по одному запросу на каждый пост."""
    await mark_viewed(session, user, payload.post_ids)
    return {"ok": True}


async def _warn_leadership(post_id: int, byline: str, author: str, words: list) -> None:
    """Сообщает руководству, что вышел пост со спорными словами.

    Не блокировка и не жалоба — просто «посмотрите». Братство академическое, и
    такой пост чаще всего окажется про историю; но узнать о нём руководство
    должно не от прочитавших.
    """
    async with async_session() as session:
        recipients = (
            await session.execute(
                select(User.telegram_id).where(
                    User.role.in_((ROLE_SUPERUSER, ROLE_FEDERAL)),
                    User.is_active.is_(True),
                    User.telegram_id.is_not(None),
                )
            )
        ).scalars().all()

    if not recipients:
        return

    text = (
        "⚠️ <b>Вышел пост со словами, требующими внимания</b>\n\n"
        f"Автор: {escape(author)}\n"
        f"Подпись: {escape(byline)}\n"
        f"Насторожило: {escape(', '.join(words))}\n\n"
        "Пост опубликован — это не блокировка, а повод посмотреть."
    )
    keyboard = None
    if WEBAPP_URL:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🏛 Открыть кабинет", web_app=WebAppInfo(url=WEBAPP_URL))]]
        )
    for telegram_id in recipients:
        await notify_telegram(telegram_id, text, reply_markup=keyboard, retries=4, retry_delay=5)


async def _mark_notifications_deleted(byline: str, messages: list) -> None:
    """Переписывает уже отправленные уведомления, когда новость удалили.

    Именно переписывает, а не удаляет: стереть своё сообщение Telegram
    разрешает боту только в первые 48 часов, а переписать — когда угодно.
    Одно правило на все случаи вместо двух, зависящих от того, сколько прошло
    времени. И честнее: новость уже прочитали, пометка говорит правду —
    было и убрали, — тогда как молча исчезнувшее сообщение сбивает с толку.

    Кнопку убираем: открывать в кабинете больше нечего.
    """
    if not messages:
        return
    bot = get_notifier_bot()
    if bot is None:
        return

    text = f"📣 <b>{escape(byline)}</b>\n\n<i>Новость удалена</i>"
    for chat_id, message_id in messages:
        try:
            await bot.edit_message_text(
                text=text, chat_id=chat_id, message_id=message_id,
                parse_mode="HTML", reply_markup=None,
            )
        except TelegramAPIError as exc:
            # Человек мог сам удалить сообщение или заблокировать бота —
            # это не повод считать удаление новости неудавшимся.
            logger.warning("Не удалось пометить уведомление %s/%s: %s", chat_id, message_id, exc)


@router.delete("/{post_id}")
async def remove(
    post_id: int,
    background_tasks: BackgroundTasks,
    personal: bool = False,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    trace = await delete_news(session, user, post_id, personal=personal)
    # В фоне: обращений к Telegram столько же, сколько было получателей, и
    # ждать их автору незачем.
    background_tasks.add_task(_mark_notifications_deleted, trace["byline"], trace["messages"])
    return {"ok": True}
