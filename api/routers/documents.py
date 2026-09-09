"""Модуль «Документы» (ТЗ §9): файлы лежат на диске сервера, в БД — метаданные."""

import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user, get_db
from api.serializers import document_dict
from config import MAX_UPLOAD_BYTES, STORAGE_DIR
from database.models import Document, Event, User
from utils.access import require_edit, require_view

router = APIRouter(prefix="/documents", tags=["documents"])

_SAFE_NAME_RE = re.compile(r"[^\w.\- ]", re.UNICODE)


def _safe_original_name(name: str) -> str:
    """Имя файла приходит от пользователя: вычищаем слэши и прочее, чем можно
    выйти из каталога хранения. Само хранимое имя всё равно генерируется заново."""
    cleaned = _SAFE_NAME_RE.sub("_", Path(name).name).strip()
    return cleaned[:255] or "file"


@router.get("")
async def list_documents(
    region_id: int,
    event_id: int | None = None,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    await require_view(session, user, region_id)

    stmt = (
        select(Document, User.full_name, Event.title)
        .join(User, User.id == Document.author_id, isouter=True)
        .join(Event, Event.id == Document.event_id, isouter=True)
        .where(Document.region_id == region_id)
    )
    if event_id is not None:
        stmt = stmt.where(Document.event_id == event_id)

    result = await session.execute(stmt.order_by(Document.created_at.desc()))
    return {
        "items": [
            document_dict(document, author_name=author, event_title=event_title)
            for document, author, event_title in result.all()
        ]
    }


@router.post("")
async def upload_document(
    region_id: int = Form(...),
    title: str = Form(...),
    doc_type: str | None = Form(default=None),
    event_id: int | None = Form(default=None),
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    await require_edit(session, user, region_id)

    if event_id is not None:
        event = await session.get(Event, event_id)
        if event is None or event.region_id != region_id:
            raise HTTPException(400, "Мероприятие не принадлежит этому региону")

    payload = await file.read()
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"Файл больше допустимых {MAX_UPLOAD_BYTES // (1024 * 1024)} МБ")
    if not payload:
        raise HTTPException(400, "Пустой файл")

    original_name = _safe_original_name(file.filename or "file")
    suffix = Path(original_name).suffix[:16]
    stored_name = f"{uuid.uuid4().hex}{suffix}"
    relative_path = Path(str(region_id)) / stored_name

    target = STORAGE_DIR / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)

    document = Document(
        region_id=region_id,
        event_id=event_id,
        title=title.strip()[:255] or original_name,
        stored_path=relative_path.as_posix(),
        original_name=original_name,
        content_type=file.content_type,
        size_bytes=len(payload),
        doc_type=(doc_type or "").strip() or None,
        author_id=user.id,
    )
    session.add(document)
    await session.commit()
    await session.refresh(document)
    return document_dict(document, author_name=user.full_name)


@router.get("/{document_id}/download")
async def download_document(
    document_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> FileResponse:
    document = await session.get(Document, document_id)
    if document is None:
        raise HTTPException(404, "Документ не найден")
    await require_view(session, user, document.region_id)

    path = (STORAGE_DIR / document.stored_path).resolve()
    if not path.is_file() or STORAGE_DIR.resolve() not in path.parents:
        raise HTTPException(404, "Файл отсутствует на диске")

    return FileResponse(
        path,
        filename=document.original_name,
        media_type=document.content_type or "application/octet-stream",
    )


@router.delete("/{document_id}")
async def delete_document(
    document_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    document = await session.get(Document, document_id)
    if document is None:
        raise HTTPException(404, "Документ не найден")
    await require_edit(session, user, document.region_id)

    path = (STORAGE_DIR / document.stored_path).resolve()
    if path.is_file() and STORAGE_DIR.resolve() in path.parents:
        path.unlink(missing_ok=True)

    await session.delete(document)
    await session.commit()
    return {"ok": True}
