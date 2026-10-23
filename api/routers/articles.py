"""Articles stay inside the existing authenticated feed; drafts are author-only."""
import json
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user, get_db
from database.models import NewsPost, User
from services.articles import (
    article_payload, article_photos, content_photo_ids, get_article, validate_content,
    prune_unused_photos, remove_pruned_files,
)
from services.moderation import check as check_text
from services.news import (
    BYLINE_PERSONAL, PHOTO_LIMIT, attach_photos, can_post_news, publication_identity,
)
from utils.access import AccessDenied

router = APIRouter(prefix="/news/articles", tags=["articles"])


class IdentityIn(BaseModel):
    official: bool = True
    region_id: int | None = None


class ArticleIn(IdentityIn):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(default="", max_length=256)
    summary: str = Field(default="", max_length=500)
    content: list = Field(default_factory=list, max_length=2500)
    revision: int = Field(ge=1)


class RevisionIn(BaseModel):
    revision: int = Field(ge=1)


async def identity(session, user, official, region_id):
    return await publication_identity(session, user, official, region_id)


async def require_saved_identity(session, user, post):
    current = await identity(session, user, post.byline_kind != BYLINE_PERSONAL, post.region_id)
    # A signature is also publication authority. It must not survive a role or
    # organisational scope change merely because the author still owns the draft.
    if post.byline_kind != BYLINE_PERSONAL and current != (post.byline, post.byline_kind):
        raise AccessDenied("Права официальной подписи изменились. Создайте новый черновик")


async def claim_revision(session, post, revision):
    result = await session.execute(update(NewsPost).where(
        NewsPost.id == post.id, NewsPost.article_revision == revision,
    ).values(article_revision=revision + 1, article_updated_at=datetime.now(timezone.utc).replace(tzinfo=None))
        .execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise HTTPException(409, "Статья уже изменена в другом окне. Откройте сохранённую версию, чтобы продолжить")


async def require_draft_capacity(session, user):
    count = (await session.execute(select(func.count(NewsPost.id)).where(
        NewsPost.author_user_id == user.id, NewsPost.publication_state == "draft",
    ))).scalar() or 0
    if count >= 30:
        raise HTTPException(409, "Сначала опубликуйте или удалите один из 30 черновиков")


@router.get("/drafts")
async def drafts(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)):
    if not can_post_news(user):
        raise AccessDenied("Черновики доступны автору в управлении")
    posts = list((await session.execute(select(NewsPost).where(
        NewsPost.author_user_id == user.id, NewsPost.content_kind == "article", NewsPost.publication_state == "draft",
    ).order_by(NewsPost.id.desc()).limit(30))).scalars())
    return {"items": [{"id": p.id, "title": p.article_title or "Без названия", "updated_at": p.article_updated_at.isoformat() + "Z" if p.article_updated_at else None} for p in posts]}


@router.post("")
async def create(payload: IdentityIn, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)):
    byline, kind = await identity(session, user, payload.official, payload.region_id)
    # A bounded author workspace, independent of the number of feed entries.
    await session.execute(select(User.id).where(User.id == user.id).with_for_update())
    await require_draft_capacity(session, user)
    post = NewsPost(author_user_id=user.id, text="", content_kind="article", publication_state="draft",
                    article_content="[]", article_title="", article_summary="", byline=byline, byline_kind=kind,
                    region_id=payload.region_id)
    session.add(post)
    await session.commit()
    await session.refresh(post)
    return await article_payload(session, user, post)


@router.get("/{article_id}")
async def read(article_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)):
    return await article_payload(session, user, await get_article(session, user, article_id))


@router.post("/{article_id}/edit")
async def start_edit(article_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)):
    source = await get_article(session, user, article_id, edit=True)
    await require_saved_identity(session, user, source)
    if source.publication_state == "draft":
        return await article_payload(session, user, source)
    await session.execute(select(User.id).where(User.id == user.id).with_for_update())
    existing = (await session.execute(select(NewsPost).where(
        NewsPost.article_source_id == source.id, NewsPost.publication_state == "draft", NewsPost.author_user_id == user.id,
    ))).scalars().first()
    if existing is not None:
        return await article_payload(session, user, existing)
    await require_draft_capacity(session, user)
    draft = NewsPost(author_user_id=user.id, text=source.text, content_kind="article", publication_state="draft",
        article_title=source.article_title, article_summary=source.article_summary, article_content=source.article_content,
        article_source_id=source.id, article_source_revision=source.article_revision,
        byline=source.byline, byline_kind=source.byline_kind, region_id=source.region_id)
    session.add(draft)
    await session.commit()
    await session.refresh(draft)
    return await article_payload(session, user, draft)


@router.put("/{article_id}")
async def save(article_id: int, payload: ArticleIn, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)):
    post = await get_article(session, user, article_id, edit=True)
    if post.publication_state != "draft":
        raise HTTPException(409, "Для изменения опубликованной статьи откройте её редактор")
    photos = {p.id for p in await article_photos(session, post)}
    content, plain = validate_content(payload.content, photos)
    title, summary = payload.title.strip(), payload.summary.strip()
    if post.article_source_id:
        # Recheck the saved scope while preserving the original published signature.
        await require_saved_identity(session, user, post)
        byline, kind = post.byline, post.byline_kind
        region_id = post.region_id
    else:
        byline, kind = await identity(session, user, payload.official, payload.region_id)
        region_id = payload.region_id
    await claim_revision(session, post, payload.revision)
    post.article_title, post.article_summary, post.article_content = title, summary, content
    post.byline, post.byline_kind, post.region_id = byline, kind, region_id
    post.text = (title + "\n" + (summary or plain[:350])).strip()
    unused = await prune_unused_photos(session, post.id, content_photo_ids(payload.content))
    await session.commit()
    remove_pruned_files(unused)
    await session.refresh(post)
    return await article_payload(session, user, post)


@router.post("/{article_id}/photos")
async def upload_photo(article_id: int, revision: int = Form(..., ge=1), file: UploadFile = File(...),
                       user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)):
    post = await get_article(session, user, article_id, edit=True)
    if post.publication_state != "draft":
        raise HTTPException(409, "Добавляйте фотографии в черновик редактирования")
    # Media changes to a published article are made while editing its document.
    used = content_photo_ids(json.loads(post.article_content or "[]"))
    count = sum(p.post_id == post.id or p.id in used for p in await article_photos(session, post))
    if count >= PHOTO_LIMIT:
        raise ValueError(f"В статье может быть не больше {PHOTO_LIMIT} фотографий")
    await claim_revision(session, post, revision)
    previous_ids = {p.id for p in await article_photos(session, post)}
    if not await attach_photos(session, post, [file], commit=False):
        raise ValueError("Выберите непустой файл фотографии")
    photo = next(p for p in await article_photos(session, post) if p.id not in previous_ids)
    # The file and its document reference commit together. If the mobile
    # connection loses the response, reopening the draft still shows the image.
    content = json.loads(post.article_content or "[]")
    content.extend([{"tag": "figure", "children": [
        {"tag": "img", "attrs": {"photo_id": photo.id}},
        {"tag": "figcaption", "children": []},
    ]}, {"tag": "p", "children": []}])
    post.article_content, _ = validate_content(content, previous_ids | {photo.id})
    await session.commit()
    await session.refresh(post)
    return await article_payload(session, user, post)


@router.post("/{article_id}/publish")
async def publish(article_id: int, payload: RevisionIn, background_tasks: BackgroundTasks,
                  user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)):
    post = await get_article(session, user, article_id, edit=True)
    if post.publication_state != "draft":
        return await article_payload(session, user, post)
    if not (post.article_title or "").strip():
        raise ValueError("Добавьте заголовок статьи")
    photos = await article_photos(session, post)
    _, plain = validate_content(json.loads(post.article_content or "[]"), {p.id for p in photos}, publishing=True)
    flagged = await check_text(session, post.article_title + "\n" + (post.article_summary or "") + "\n" + plain)
    # Recheck region permissions at publication, after potentially long draft editing.
    await require_saved_identity(session, user, post)
    if post.article_source_id:
        published = await get_article(session, user, post.article_source_id, edit=True)
        # Same lock order as source deletion: published post, then working copy.
        await claim_revision(session, published, post.article_source_revision)
        await claim_revision(session, post, payload.revision)
        published.article_title, published.article_summary = post.article_title, post.article_summary
        published.article_content, published.text = post.article_content, post.text
        for photo in photos:
            if photo.post_id == post.id:
                photo.post_id = published.id
        await session.flush()
        unused = await prune_unused_photos(session, published.id, content_photo_ids(json.loads(post.article_content)))
        await session.delete(post)
        await session.commit()
        remove_pruned_files(unused)
        await session.refresh(published)
        if flagged:
            from api.routers.news import _warn_leadership
            background_tasks.add_task(_warn_leadership, published.id, published.byline, user.full_name, list(flagged))
        return await article_payload(session, user, published)
    await claim_revision(session, post, payload.revision)
    published = NewsPost(author_user_id=user.id, text=post.text, byline=post.byline, byline_kind=post.byline_kind,
        region_id=post.region_id, content_kind="article", publication_state="published",
        article_title=post.article_title, article_summary=post.article_summary, article_content=post.article_content,
        article_revision=1, article_updated_at=datetime.now(timezone.utc).replace(tzinfo=None))
    session.add(published)
    await session.flush()
    for photo in photos:
        photo.post_id = published.id
    await session.flush()
    unused = await prune_unused_photos(session, published.id, content_photo_ids(json.loads(post.article_content)))
    await session.delete(post)
    await session.commit()
    remove_pruned_files(unused)
    await session.refresh(published)
    from api.routers.news import _send_news_notifications
    background_tasks.add_task(_send_news_notifications, published.id)
    if flagged:
        from api.routers.news import _warn_leadership
        background_tasks.add_task(_warn_leadership, published.id, published.byline, user.full_name, list(flagged))
    return await article_payload(session, user, published)
