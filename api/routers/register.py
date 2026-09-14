"""Регистрация — веб-форма Mini App вместо чата (план «Снизу вверх»).

Настоящий отбор («посвящение») идёт на внешнем сайте (academists.ru) — вне
этого проекта. Сюда попадает уже принятый человек, чтобы завести себе личный
кабинет: анкета короче и проще анкеты отбора (ФИО, дата рождения, телефон,
отделение/ВУЗ/факультет/курс), но всё равно проходит лёгкое подтверждение
руководителем внутри бота (MembershipApplication, как и раньше) — у бота нет
иного способа убедиться, что отбор на сайте для этого telegram_id и правда
состоялся."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import TelegramIdentity, get_db, get_telegram_identity
from config import AUTO_FEDERAL_TELEGRAM_IDS, PRIMARY_REVIEWER_FULL_NAME
from database.models import (
    APPLICATION_STATE_PENDING,
    EDUCATION_LEVEL_LABELS,
    MEMBER_STATUS_LABELS,
    ROLE_FEDERAL,
    MembershipApplication,
    Region,
    University,
    User,
)
from services.admin_actions import create_federal
from services.applications import approve_application
from utils.notify import escape_telegram_html, notify_telegram, send_cabinet_welcome
from utils.parser import normalize_telegram_username
from utils.roles import region_display_name
from utils.users import resolve_user

router = APIRouter(prefix="/register", tags=["register"])


@router.get("/context")
async def register_context(
    identity: TelegramIdentity = Depends(get_telegram_identity),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Веб решает, что показывать: уже есть кабинет — сразу на него; иначе —
    список отделений для формы."""
    existing = await resolve_user(session, identity.telegram_id, identity.full_name)
    if existing is not None:
        return {"already_registered": True}

    regions = list(
        (await session.execute(select(Region).where(Region.is_active.is_(True)).order_by(Region.name)))
        .scalars()
        .all()
    )
    return {
        "already_registered": False,
        "regions": [{"id": r.id, "label": region_display_name(r)} for r in regions],
    }


def _university_dict(university: University) -> dict:
    return {"id": university.id, "name": university.name}


@router.get("/universities")
async def register_universities(
    region_id: int,
    q: str = Query(default="", max_length=255),
    identity: TelegramIdentity = Depends(get_telegram_identity),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Строго внутри выбранного отделения — до выбора региона список вузов не
    отдаём вовсе (план §3): человек из Новосибирска не должен увидеть МГИМО."""
    stmt = select(University).where(University.region_id == region_id).order_by(University.name)
    items = list((await session.execute(stmt.limit(5_000))).scalars().all())
    needle = q.strip().lower()
    if needle:
        items = [u for u in items if needle in u.name.lower()]
    return {"items": [_university_dict(u) for u in items[:20]]}


class RegisterUniversityIn(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    region_id: int


@router.post("/universities")
async def register_create_university(
    payload: RegisterUniversityIn,
    identity: TelegramIdentity = Depends(get_telegram_identity),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Публичная регистрация не изменяет справочники приложения.

    Параметры и зависимость Telegram оставлены для совместимости клиентов:
    неизвестный вуз добавляет руководитель после проверки названия.
    """
    region = await session.get(Region, payload.region_id)
    if region is None or not region.is_active:
        raise HTTPException(400, "Отделение не найдено")
    raise HTTPException(403, "ВУЗ отсутствует в справочнике — обратитесь к руководителю отделения")


class RegisterSubmit(BaseModel):
    # Все поля обязательны — это уже сокращённая анкета (у внешнего отбора на
    # сайте нет факультета/курса, но раз уж мы их спрашиваем — не пропускать).
    full_name: str = Field(min_length=2, max_length=128)
    # Текстом в формате «20.02.2000», не native date picker (см. план §2).
    birth_date: str = Field(min_length=1, max_length=16)
    phone: str = Field(min_length=1, max_length=32)
    telegram_username: str = Field(min_length=2, max_length=33)
    region_id: int
    university_id: int
    faculty: str = Field(min_length=1, max_length=255)
    # 1-6 либо «Окончил» (graduated_university=True, курс тогда пустой) —
    # тот же выбор, что и в «Составе» (api/routers/members.py).
    course: int | None = Field(default=None, ge=1, le=6)
    graduated_university: bool = False
    # Обязательно, только если «Окончил» — проверяется ниже, не через Field,
    # т.к. зависит от значения другого поля.
    workplace: str | None = Field(default=None, max_length=255)
    # Обязательно, только если НЕ «Окончил» — раз человек больше не учится,
    # уровень обучения теряет смысл (webapp/app.js скрывает поле в этом случае).
    education_level: str | None = Field(default=None, max_length=32)
    # Человек выбирает сам, в самом конце анкеты — раньше руководитель
    # выставлял «активиста» по умолчанию при одобрении, это изменено:
    # от статуса зависят вкладки личного кабинета на будущих этапах.
    status: str = Field(max_length=16)


@router.post("/submit")
async def register_submit(
    payload: RegisterSubmit,
    identity: TelegramIdentity = Depends(get_telegram_identity),
    session: AsyncSession = Depends(get_db),
) -> dict:
    existing = await resolve_user(session, identity.telegram_id, identity.full_name)
    if existing is not None:
        raise HTTPException(409, "У вас уже есть личный кабинет")

    region = await session.get(Region, payload.region_id)
    if region is None or not region.is_active:
        raise HTTPException(400, "Отделение не найдено")
    university = await session.get(University, payload.university_id)
    if university is None or university.region_id != region.id:
        raise HTTPException(400, "ВУЗ не принадлежит выбранному отделению")

    # Строго ДД.ММ.ГГГГ с годом — в отличие от utils/parser.py::parse_date_hint
    # (тот для быстрого ввода в чате и год не требует), у формальной анкеты
    # маска на вводе (webapp/app.js::applyDateMask) уже гарантирует этот
    # формат, так что сервер просто проверяет то же самое ещё раз.
    try:
        birth_date = datetime.strptime(payload.birth_date.strip(), "%d.%m.%Y").date()
    except ValueError as exc:
        raise HTTPException(400, "Не удалось разобрать дату рождения — формат ДД.ММ.ГГГГ") from exc

    try:
        telegram_username = normalize_telegram_username(payload.telegram_username)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    if payload.status not in MEMBER_STATUS_LABELS:
        raise HTTPException(400, "Неизвестный статус")
    if payload.graduated_university:
        if not (payload.workplace or "").strip():
            raise HTTPException(400, "Укажите место работы")
    else:
        if payload.course is None:
            raise HTTPException(400, "Укажите курс")
        if payload.education_level not in EDUCATION_LEVEL_LABELS:
            raise HTTPException(400, "Неизвестный уровень обучения")

    pending = (
        await session.execute(
            select(MembershipApplication).where(
                MembershipApplication.telegram_id == identity.telegram_id,
                MembershipApplication.state == APPLICATION_STATE_PENDING,
            )
        )
    ).scalar_one_or_none()
    if pending is not None:
        raise HTTPException(409, "Анкета уже отправлена и ждёт подтверждения руководителя")

    application = MembershipApplication(
        region_id=region.id,
        telegram_id=identity.telegram_id,
        full_name=payload.full_name.strip(),
        phone=payload.phone.strip(),
        telegram_username=telegram_username,
        birth_date=birth_date,
        university_id=payload.university_id,
        faculty=payload.faculty.strip(),
        course=payload.course,
        graduated_university=payload.graduated_university,
        workplace=(payload.workplace or "").strip() or None,
        education_level=None if payload.graduated_university else payload.education_level,
        member_status=payload.status,
    )
    session.add(application)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, "Анкета уже отправлена и ждёт подтверждения руководителя") from None
    await session.refresh(application)

    if identity.telegram_id in AUTO_FEDERAL_TELEGRAM_IDS:
        # Доверенные люди-администраторы (план «Убираем технического
        # superuser», config.AUTO_FEDERAL_TELEGRAM_IDS) — анкету подтверждает
        # не руководитель, а сама регистрация, и сразу выдаёт роль federal
        # (member_id уже проставлен approve_application — _resolve_or_promote
        # повышает роль на месте, не создавая второй аккаунт).
        approved_user = await approve_application(session, application.id, reviewer_id=None)
        await create_federal(session, member_id=approved_user.member_id)
        await send_cabinet_welcome(approved_user)
        await notify_telegram(
            approved_user.telegram_id,
            "Вам назначена роль федерального координатора — в меню появятся "
            "«🕵 Войти как...» и «⚙️ Управление», в кабинете — вкладка «Регионы».",
        )
        return {"ok": True}

    summary_lines = [
        f"ФИО: {escape_telegram_html(application.full_name)}",
        f"Телефон: {escape_telegram_html(application.phone)}",
        f"Telegram: {escape_telegram_html(application.telegram_username)}",
        f"Дата рождения: {birth_date.strftime('%d.%m.%Y')}",
        f"ВУЗ: {escape_telegram_html(university.name if university else '?')}",
        f"Факультет: {escape_telegram_html(application.faculty)}",
        f"Курс: {'окончил' if application.graduated_university else application.course}",
        f"Статус: {MEMBER_STATUS_LABELS[application.member_status]}",
    ]
    if application.education_level:
        summary_lines.insert(-1, f"Уровень: {EDUCATION_LEVEL_LABELS[application.education_level]}")
    if application.workplace:
        summary_lines.append(f"Место работы: {escape_telegram_html(application.workplace)}")

    notify_text = (
        f"📝 <b>Подтверждение личного кабинета — {escape_telegram_html(region.name)}</b>\n\n"
        + "\n".join(summary_lines)
    )
    reviewer = await _pick_reviewer(session, region)
    if reviewer is not None:
        await notify_telegram(reviewer.telegram_id, notify_text)

    # Подтверждение в чат бота — отдельно от текста внутри самой Mini App
    # (webapp/app.js::renderRegisterView), явно попросили именно сообщение в чат.
    await notify_telegram(identity.telegram_id, "Заявка отправлена, ожидайте.")

    return {"ok": True}


async def _pick_reviewer(session: AsyncSession, region: Region) -> User | None:
    """Кому уходит уведомление о новой анкете. Временно (план «Убираем
    технического superuser», PRIMARY_REVIEWER_FULL_NAME) — все анкеты одному
    конкретному человеку, руководители регионов пока не подтверждают сами
    (см. handlers/apply.py::_require_reviewer — там та же граница). Без
    настройки — прежнее поведение: руководителю региона, без него — любому
    активному federal."""
    if PRIMARY_REVIEWER_FULL_NAME:
        # В Python, не в SQL: lower() в SQLite не приводит кириллицу к
        # нижнему регистру (тот же нюанс, что и в api/routers/members.py).
        needle = PRIMARY_REVIEWER_FULL_NAME.lower()
        candidates = (await session.execute(select(User).where(User.is_active.is_(True)))).scalars().all()
        for candidate in candidates:
            if candidate.full_name.strip().lower() == needle:
                return candidate

    if region.leader_user_id:
        return await session.get(User, region.leader_user_id)
    return (
        await session.execute(select(User).where(User.role == ROLE_FEDERAL, User.is_active.is_(True)))
    ).scalars().first()
