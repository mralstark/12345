"""Модуль «Документы» (ТЗ §9): файлы лежат на диске сервера, в БД — метаданные."""

import re
import uuid
import zipfile
from io import BytesIO
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
from utils.storage import ensure_storage_capacity

router = APIRouter(prefix="/documents", tags=["documents"])

_SAFE_NAME_RE = re.compile(r"[^\w.\- ]", re.UNICODE)
_CHUNK_SIZE = 1024 * 1024
_ZIP_MAX_FILES = 2_000
_ZIP_MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
_OFFICE_TYPES = {
    ".docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "word/"),
    ".xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xl/"),
    ".pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", "ppt/"),
}
_TEXT_TYPES = {".txt": "text/plain", ".csv": "text/csv"}
_OFFICE_ACTIVE_PARTS = ("vbaproject.bin", "/embeddings/", "/activex/", "/externallinks/")
_EXTERNAL_RELATION_RE = re.compile(rb"\bTargetMode\s*=\s*['\"]External['\"]", re.IGNORECASE)


def _safe_original_name(name: str) -> str:
    """Имя файла приходит от пользователя: вычищаем слэши и прочее, чем можно
    выйти из каталога хранения. Само хранимое имя всё равно генерируется заново."""
    cleaned = _SAFE_NAME_RE.sub("_", Path(name).name).strip()
    return cleaned[:255] or "file"


async def _read_limited(upload: UploadFile) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while chunk := await upload.read(_CHUNK_SIZE):
        size += len(chunk)
        if size > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"Файл больше допустимых {MAX_UPLOAD_BYTES // (1024 * 1024)} МБ")
        chunks.append(chunk)
    if not chunks:
        raise HTTPException(400, "Пустой файл")
    return b"".join(chunks)


def _validated_document(payload: bytes, original_name: str) -> tuple[str, str]:
    """Возвращает серверный MIME и каноническое расширение. Content-Type от
    клиента не используется: его может указать атакующий."""
    suffix = Path(original_name).suffix.lower()
    if suffix == ".pdf":
        if not payload.startswith(b"%PDF-") or b"%%EOF" not in payload[-4096:]:
            raise HTTPException(400, "Файл не является корректным PDF")
        return "application/pdf", suffix

    if suffix in _TEXT_TYPES:
        if b"\x00" in payload:
            raise HTTPException(400, "Текстовый файл содержит двоичные данные")
        try:
            payload.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                payload.decode("cp1251")
            except UnicodeDecodeError as exc:
                raise HTTPException(400, "Не удалось определить кодировку текста") from exc
        return _TEXT_TYPES[suffix], suffix

    if suffix in _OFFICE_TYPES:
        try:
            with zipfile.ZipFile(BytesIO(payload)) as archive:
                entries = archive.infolist()
                if len(entries) > _ZIP_MAX_FILES:
                    raise HTTPException(400, "Слишком много файлов внутри документа")
                if sum(item.file_size for item in entries) > _ZIP_MAX_UNCOMPRESSED_BYTES:
                    raise HTTPException(400, "Слишком большой распакованный документ")
                if any(item.flag_bits & 1 for item in entries):
                    raise HTTPException(400, "Зашифрованные Office-документы не поддерживаются")
                names = {item.filename.replace("\\", "/") for item in entries}
                lowered_names = {name.lower() for name in names}
                if any("\\" in item.filename for item in entries) or any(
                    name.startswith("/") or "../" in f"/{name}" for name in names
                ):
                    raise HTTPException(400, "Некорректные пути внутри Office-документа")
                if any(part in name for name in lowered_names for part in _OFFICE_ACTIVE_PARTS):
                    raise HTTPException(400, "Office-документы с активным содержимым не поддерживаются")
                if "[Content_Types].xml" not in names:
                    raise HTTPException(400, "Некорректная структура Office-документа")
                expected_mime, required_prefix = _OFFICE_TYPES[suffix]
                if not any(name.startswith(required_prefix) for name in names):
                    raise HTTPException(400, "Расширение не соответствует содержимому документа")
                if expected_mime.encode() not in archive.read("[Content_Types].xml"):
                    raise HTTPException(400, "Расширение не соответствует типу Office-документа")
                for name in names:
                    if name.lower().endswith(".rels") and _EXTERNAL_RELATION_RE.search(archive.read(name)):
                        raise HTTPException(400, "Внешние связи в Office-документах не поддерживаются")
        except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
            raise HTTPException(400, "Файл не является корректным Office-документом") from exc
        return _OFFICE_TYPES[suffix][0], suffix

    raise HTTPException(400, "Разрешены PDF, DOCX, XLSX, PPTX, TXT и CSV")


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

    original_name = _safe_original_name(file.filename or "file")
    payload = await _read_limited(file)
    content_type, suffix = _validated_document(payload, original_name)
    try:
        ensure_storage_capacity(len(payload))
    except ValueError as exc:
        raise HTTPException(507, str(exc)) from exc
    clean_doc_type = (doc_type or "").strip()
    if len(clean_doc_type) > 32:
        raise HTTPException(422, "Тип документа длиннее 32 символов")
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
        content_type=content_type,
        size_bytes=len(payload),
        doc_type=clean_doc_type or None,
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
