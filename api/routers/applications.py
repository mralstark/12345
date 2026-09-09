"""Вкладка «Заявки» в Mini App — подтверждение анкет саморегистрации без
захода в чат бота (handlers/apply.py по-прежнему работает, это второй вход
в тот же MembershipApplication, не замена).

Права: пока только superuser (обкатываем интерфейс перед тем, как отдавать
его federal — см. handlers/apply.py::_require_reviewer, где federal уже
проверяет анкеты в боте). Расширить на federal — просто добавить роль
в _CAN_REVIEW.

Правка полей анкеты — те же поля, что и у «Исправить» в боте (handlers/apply.py
::_EDIT_FIELDS): ФИО, телефон, дата рождения, факультет, место работы."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user, get_db
from api.serializers import application_dict
from database.models import APPLICATION_STATE_PENDING, ROLE_SUPERUSER, MembershipApplication, Region, University, User
from services.applications import approve_application as svc_approve_application
from services.applications import reject_application as svc_reject_application
from utils.notify import notify_telegram, send_cabinet_welcome

router = APIRouter(prefix="/applications", tags=["applications"])

_CAN_REVIEW = (ROLE_SUPERUSER,)


class ApplicationPatch(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=128)
    phone: str | None = Field(default=None, max_length=32)
    birth_date: date | None = None
    faculty: str | None = Field(default=None, max_length=255)
    workplace: str | None = Field(default=None, max_length=255)


@router.get("/pending")
async def list_pending_applications(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    if user.role not in _CAN_REVIEW:
        raise HTTPException(403, "Недоступно")

    rows = (
        await session.execute(
            select(MembershipApplication)
            .where(MembershipApplication.state == APPLICATION_STATE_PENDING)
            .order_by(MembershipApplication.created_at)
        )
    ).scalars().all()

    region_ids = {a.region_id for a in rows}
    university_ids = {a.university_id for a in rows if a.university_id is not None}
    regions = {}
    if region_ids:
        regions = {
            r.id: r.name
            for r in (await session.execute(select(Region).where(Region.id.in_(region_ids)))).scalars().all()
        }
    universities = {}
    if university_ids:
        universities = {
            u.id: u.name
            for u in (await session.execute(select(University).where(University.id.in_(university_ids)))).scalars().all()
        }

    return {
        "items": [
            application_dict(a, regions.get(a.region_id), universities.get(a.university_id))
            for a in rows
        ]
    }


@router.patch("/{application_id}")
async def edit_application(
    application_id: int,
    payload: ApplicationPatch,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    if user.role not in _CAN_REVIEW:
        raise HTTPException(403, "Недоступно")

    application = await session.get(MembershipApplication, application_id)
    if application is None:
        raise HTTPException(404, "Анкета не найдена")
    if application.state != APPLICATION_STATE_PENDING:
        raise HTTPException(400, "Анкета уже рассмотрена")

    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        if isinstance(value, str):
            value = value.strip() or None
        setattr(application, field, value)
    await session.commit()
    await session.refresh(application)

    region = await session.get(Region, application.region_id)
    university = await session.get(University, application.university_id) if application.university_id else None
    return application_dict(application, region.name if region else None, university.name if university else None)


@router.post("/{application_id}/approve")
async def approve_application(
    application_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    if user.role not in _CAN_REVIEW:
        raise HTTPException(403, "Недоступно")

    application = await session.get(MembershipApplication, application_id)
    if application is None:
        raise HTTPException(404, "Анкета не найдена")

    approved_user = await svc_approve_application(session, application_id, user.id)
    await send_cabinet_welcome(approved_user)
    return {"ok": True}


@router.post("/{application_id}/reject")
async def reject_application(
    application_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    if user.role not in _CAN_REVIEW:
        raise HTTPException(403, "Недоступно")

    application = await session.get(MembershipApplication, application_id)
    if application is None:
        raise HTTPException(404, "Анкета не найдена")

    await svc_reject_application(session, application_id, user.id)
    await notify_telegram(
        application.telegram_id,
        "❌ Подтверждение личного кабинета отклонено. Если это ошибка — напишите администратору.",
    )
    return {"ok": True}
