"""Экспорт отчёта по региону одним файлом: XLSX или PDF (ТЗ §13)."""

import asyncio
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user, get_db
from config import STORAGE_DIR
from database.models import Document, Region, User
from services.reports import gather_region_report
from utils.access import actor_cell, require_view
from utils.period import resolve_period
from utils.report_export import build_region_pdf, build_region_xlsx
from services.report_downloads import cache_dir, decode_ticket, generation_slots, save_export

router = APIRouter(prefix="/reports", tags=["reports"])


def _content_disposition(filename: str) -> str:
    """Кириллица в имени файла — только через RFC 5987, иначе браузер получит мусор."""
    extension = filename.rsplit(".", 1)[-1] if "." in filename else "bin"
    return f"attachment; filename=\"report.{extension}\"; filename*=UTF-8''{quote(filename, safe='')}"


async def _build_region_report(
    region_id: int,
    format: str,
    period: str,
    offset: int,
    user: User,
    session: AsyncSession,
) -> Response:
    await require_view(session, user, region_id)
    if await actor_cell(session, user) is not None:
        raise HTTPException(403, "Полный отчёт региона недоступен руководителю вузовской ячейки")
    if format not in ("xlsx", "pdf"):
        raise HTTPException(400, "Формат отчёта — xlsx или pdf")

    start, end, label = resolve_period(period, offset)
    report = await gather_region_report(session, region_id, start, end, label)

    region = await session.get(Region, region_id)
    safe_region = (region.name if region else str(region_id)).replace(" ", "_")

    # Сборка файла — в отдельном потоке: и Excel, и PDF считаются секундами, а
    # ядро на сервере одно. Пока отчёт строился прямо здесь, кабинет замирал
    # для всех остальных — не только для того, кто заказал отчёт.
    if format == "xlsx":
        payload = await asyncio.to_thread(build_region_xlsx, report)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        payload = await asyncio.to_thread(build_region_pdf, report)
        media_type = "application/pdf"

    filename = f"Отчёт_{safe_region}_{start:%Y-%m-%d}_{end:%Y-%m-%d}.{format}"
    return Response(
        content=payload,
        media_type=media_type,
        headers={"Content-Disposition": _content_disposition(filename)},
    )


@router.get("/region")
async def region_report(
    region_id: int,
    format: str = Query(default="xlsx", max_length=8),
    period: str = Query(default="year", max_length=16),
    offset: int = Query(default=0, ge=-1200, le=1200),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    if generation_slots.locked():
        raise HTTPException(429, "Отчёты уже формируются. Попробуйте немного позже")
    async with generation_slots:
        return await _build_region_report(region_id, format, period, offset, user, session)


@router.post("/region/file")
async def prepare_region_report(
    region_id: int,
    format: str = Query(default="xlsx", max_length=8),
    period: str = Query(default="year", max_length=16),
    offset: int = Query(default=0, ge=-1200, le=1200),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    # Backpressure before any expensive work; API reads stay responsive.
    if generation_slots.locked():
        raise HTTPException(429, "Отчёты уже формируются. Попробуйте немного позже")
    async with generation_slots:
        response = await _build_region_report(region_id, format, period, offset, user, session)
        filename = f"report_{region_id}.{format}"
        ticket = await save_export(response.body, user.id, region_id, format, filename)
    file_id = decode_ticket(ticket)["file"]
    return {"url": f"/api/reports/files/{file_id}?t={ticket}", "filename": filename, "expires_in": 600}


@router.api_route("/files/{file_id}", methods=["GET", "HEAD"])
async def download_region_report(
    file_id: str, t: str = Query(default="", max_length=2048),
    session: AsyncSession = Depends(get_db),
) -> FileResponse:
    from api.rate_limit import enforce_user_rate_limit
    data = decode_ticket(t)
    if file_id != data["file"]:
        raise HTTPException(403, "Ссылка недействительна")
    user = await session.get(User, data["uid"])
    if user is None or not user.is_active:
        raise HTTPException(403, "Доступ закрыт")
    enforce_user_rate_limit(user.telegram_id, "GET", "/api/downloads")
    await require_view(session, user, data["rid"])
    cell = await actor_cell(session, user)
    kind = data.get("kind", "report")
    if kind == "report" and cell is not None:
        raise HTTPException(403, "Нет доступа к полному отчёту региона")
    if kind == "finance" and cell is not None and cell.id != data.get("cell_id"):
        raise HTTPException(403, "Область доступа к финансовому отчёту изменилась")
    if kind == "document":
        document = await session.get(Document, data.get("document_id"))
        if document is None or document.region_id != data["rid"]:
            raise HTTPException(404, "Документ удалён или перемещён")
    if kind not in {"report", "finance", "document"}:
        raise HTTPException(403, "Ссылка недействительна")
    path = cache_dir() / data["file"]
    if not path.is_file():
        raise HTTPException(410, "Файл больше недоступен. Сформируйте отчёт заново")
    if kind == "document":
        path = (STORAGE_DIR / document.stored_path).resolve()
        if STORAGE_DIR.resolve() not in path.parents or not path.is_file():
            raise HTTPException(404, "Файл отсутствует на диске")
    media_type = {".pdf": "application/pdf", ".csv": "text/csv; charset=utf-8", ".txt": "text/plain; charset=utf-8",
                  ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                  ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                  ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}[path.suffix]
    return FileResponse(path, media_type=media_type,
                        headers={"Content-Disposition": _content_disposition(data["name"]),
                                 "Cache-Control": "no-store", "Access-Control-Allow-Origin": "https://web.telegram.org"})
