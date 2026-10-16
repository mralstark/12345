"""Bounded Telegraph-style documents. No author-supplied HTML is stored/rendered."""
import json
import math
import logging
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from config import STORAGE_DIR, WEBAPP_URL

from database.models import NewsPhoto, NewsPost, User
from services.news import can_post_news, can_see_post, photo_url
from utils.access import AccessDenied
from utils.tz import iso_utc

MAX_CONTENT_BYTES = 128 * 1024
MAX_TEXT = 60_000
TAGS = {"p", "h3", "h4", "blockquote", "ul", "ol", "li", "b", "strong", "i", "em", "a", "br", "hr", "figure", "figcaption", "img"}


def article_link(post_id):
    url = urlsplit(WEBAPP_URL or "/")
    params = dict(parse_qsl(url.query))
    params["article"] = str(post_id)
    return urlunsplit((url.scheme, url.netloc, url.path or "/", urlencode(params), ""))


async def prune_unused_photos(session, post_id, keep_ids):
    paths = []
    for photo in (await session.execute(select(NewsPhoto).where(NewsPhoto.post_id == post_id))).scalars():
        if photo.id not in keep_ids:
            paths.append(photo.stored_path)
            await session.delete(photo)
    return paths


def remove_pruned_files(paths):
    for path in paths:
        target = (STORAGE_DIR / path).resolve()
        if STORAGE_DIR.resolve() in target.parents:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                logging.getLogger(__name__).warning("Could not remove unused article illustration")


def cover_photo_id(post):
    def find(nodes):
        for node in nodes:
            if isinstance(node, dict):
                if node.get("tag") == "img":
                    return node.get("attrs", {}).get("photo_id")
                found = find(node.get("children", []))
                if found:
                    return found
        return None
    return find(json.loads(post.article_content or "[]")) if post.content_kind == "article" else None


def content_photo_ids(nodes):
    ids = set()
    for node in nodes:
        if isinstance(node, dict):
            if node.get("tag") == "img":
                ids.add(node["attrs"]["photo_id"])
            ids.update(content_photo_ids(node.get("children", [])))
    return ids


def safe_link(value: str) -> bool:
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 33 for c in value) or "\\" in value:
        return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme in ("https", "http") and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False


def validate_content(content: list, photo_ids: set[int], *, publishing: bool = False) -> tuple[str, str]:
    if not isinstance(content, list):
        raise ValueError("Не удалось прочитать текст статьи")
    count = 0
    text_length = 0
    text = []

    def walk(node, depth=0):
        nonlocal count, text_length
        count += 1
        if count > 2500 or depth > 12:
            raise ValueError("Слишком сложное форматирование статьи")
        if isinstance(node, str):
            text_length += len(node)
            if text_length > MAX_TEXT:
                raise ValueError("В статье может быть не больше 60 000 знаков")
            text.append(node)
            return
        if not isinstance(node, dict) or set(node) - {"tag", "attrs", "children"} or not isinstance(node.get("tag"), str) or node["tag"] not in TAGS:
            raise ValueError("В статье есть неподдерживаемое форматирование")
        tag = node["tag"]
        attrs = node.get("attrs", {})
        children = node.get("children", [])
        if not isinstance(attrs, dict) or not isinstance(children, list):
            raise ValueError("Не удалось прочитать форматирование статьи")
        if tag == "a":
            if set(attrs) != {"href"} or not safe_link(attrs["href"]):
                raise ValueError("Ссылка должна начинаться с https:// или http://")
        elif tag == "img":
            if set(attrs) != {"photo_id"} or type(attrs["photo_id"]) is not int or attrs["photo_id"] not in photo_ids or children:
                raise ValueError("Используйте фотографию, загруженную в эту статью")
        elif attrs:
            raise ValueError("Лишние параметры форматирования статьи")
        if tag in ("br", "hr") and children:
            raise ValueError("Некорректный разделитель статьи")
        for child in children:
            walk(child, depth + 1)
        if tag in ("p", "h3", "h4", "blockquote", "li", "figcaption", "br"):
            text.append("\n")

    for node in content:
        walk(node)
    plain = "".join(text).strip()
    if len(plain) > MAX_TEXT:
        raise ValueError("В статье может быть не больше 60 000 знаков")
    if publishing and not plain:
        raise ValueError("Добавьте текст статьи")
    if len(content_photo_ids(content)) > 10:
        raise ValueError("В статье может быть не больше 10 фотографий")
    encoded = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode()) > MAX_CONTENT_BYTES:
        raise ValueError("Статья слишком большая. Сократите текст или число элементов")
    return encoded, plain


async def get_article(session: AsyncSession, user: User, article_id: int, *, edit: bool = False) -> NewsPost:
    from fastapi import HTTPException
    post = await session.get(NewsPost, article_id)
    if post is None or post.content_kind != "article":
        raise HTTPException(404, "Статья не найдена")
    if not await can_see_post(session, user, post):
        raise AccessDenied("Статья недоступна")
    if edit and (post.author_user_id != user.id or not can_post_news(user)):
        raise AccessDenied("Статью редактирует её автор из кабинета управления")
    return post


async def article_photos(session: AsyncSession, post: NewsPost):
    ids = [post.id]
    if post.publication_state == "draft" and post.article_source_id:
        ids.append(post.article_source_id)
    return list((await session.execute(select(NewsPhoto).where(NewsPhoto.post_id.in_(ids)).order_by(NewsPhoto.id))).scalars())


async def article_payload(session: AsyncSession, user: User, post: NewsPost) -> dict:
    photos = await article_photos(session, post)
    content = json.loads(post.article_content or "[]")
    _, plain = validate_content(content, {p.id for p in photos})
    return {
        "id": post.id, "title": post.article_title or "", "summary": post.article_summary or "",
        "content": content, "revision": post.article_revision, "state": post.publication_state,
        "byline": post.byline, "byline_kind": post.byline_kind, "region_id": post.region_id,
        "created_at": iso_utc(post.created_at), "updated_at": iso_utc(post.article_updated_at),
        "can_edit": post.author_user_id == user.id and can_post_news(user),
        "source_id": post.article_source_id,
        "reading_minutes": max(1, math.ceil(len(plain.split()) / 180)),
        "photos": [{"id": p.id, "url": photo_url(p.id)} for p in photos],
    }
