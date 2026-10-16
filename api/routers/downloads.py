"""Prepare finance exports and documents for the same native download flow."""
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user, get_db
from api.routers.documents import download_document
from api.routers.finance import export_csv
from database.models import Document, User
from services.report_downloads import decode_ticket, generation_slots, save_export
from utils.access import actor_cell

router = APIRouter(prefix="/downloads", tags=["downloads"])


class DownloadIn(BaseModel):
    kind: Literal["finance", "document"]
    region_id: int | None = None
    document_id: int | None = None
    period: str = Field(default="year", max_length=16)
    offset: int = Field(default=0, ge=-1200, le=1200)


@router.post("/prepare")
async def prepare(
    payload: DownloadIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    if generation_slots.locked():
        raise HTTPException(429, "Файлы уже формируются. Попробуйте немного позже")
    async with generation_slots:
        if payload.kind == "finance":
            if payload.region_id is None:
                raise HTTPException(422, "Укажите регион")
            response = await export_csv(payload.region_id, payload.period, payload.offset, user, session)
            content, suffix, filename = response.body, "csv", f"finance_{payload.region_id}.csv"
            region_id = payload.region_id
        else:
            if payload.document_id is None:
                raise HTTPException(422, "Укажите документ")
            response = await download_document(payload.document_id, user, session)
            document = await session.get(Document, payload.document_id)
            region_id = document.region_id
            suffix = Path(response.path).suffix.lower().lstrip(".")
            if suffix not in {"pdf", "xlsx", "docx", "pptx", "txt", "csv"}:
                raise HTTPException(400, "Формат документа не поддерживается")
            filename = document.original_name
            # The document is already stored privately. Cache only a zero-byte
            # capability marker; do not copy large documents into RAM/disk.
            content = b""
        cell = await actor_cell(session, user)
        ticket = await save_export(content, user.id, region_id, suffix, filename,
                                   kind=payload.kind, cell_id=cell.id if cell else None,
                                   document_id=payload.document_id if payload.kind == "document" else None)
    return {"url": f"/api/reports/files/{decode_ticket(ticket)['file']}?t={ticket}",
            "filename": filename, "expires_in": 600}
