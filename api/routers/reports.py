"""Экспорт отчёта по региону одним файлом: XLSX или PDF (ТЗ §13)."""

import asyncio
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user, get_db
from database.models import Region, User
from services.reports import gather_region_report
from utils.access import require_view
from utils.period import resolve_period
from utils.report_export import build_region_pdf, build_region_xlsx

router = APIRouter(prefix="/reports", tags=["reports"])


def _content_disposition(filename: str) -> str:
    """Кириллица в имени файла — только через RFC 5987, иначе браузер получит мусор."""
    return f"attachment; filename=\"report\"; filename*=UTF-8''{quote(filename)}"


@router.get("/region")
async def region_report(
    region_id: int,
    format: str = "xlsx",
    period: str = "year",
    offset: int = 0,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    await require_view(session, user, region_id)
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
