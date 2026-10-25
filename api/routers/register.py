"""Регистрация — веб-форма Mini App вместо чата (план «Снизу вверх»).

Настоящий отбор («посвящение») идёт на внешнем сайте (academists.ru) — вне
этого проекта. Сюда попадает уже принятый человек, чтобы завести себе личный
кабинет: анкета короче и проще анкеты отбора (ФИО, дата рождения, телефон,
отделение/ВУЗ/факультет/курс), но всё равно проходит лёгкое подтверждение
руководителем внутри бота (MembershipApplication, как и раньше) — у бота нет
иного способа убедиться, что отбор на сайте для этого telegram_id и правда
состоялся."""

import unicodedata
import re

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import TelegramIdentity, get_db, get_telegram_identity
from config import AUTO_FEDERAL_TELEGRAM_IDS, IS_PRODUCTION, PRIMARY_REVIEWER_FULL_NAME
from database.models import (
    APPLICATION_STATE_PENDING,
    EDUCATION_LEVEL_LABELS,
    MEMBER_STATUS_LABELS,
    ROLE_FEDERAL,
    ROLE_SUPERUSER,
    BureauMember,
    BureauRegion,
    CoordinatorRegion,
    MembershipApplication,
    ExternalIdentity,
    Region,
    TelegramPhoneVerification,
    University,
    UniversityCell,
    User,
)
from services.admin_actions import create_federal
from services.applications import approve_application
from utils.notify import escape_telegram_html, notify_telegram, send_cabinet_welcome
from utils.parser import normalize_phone, normalize_telegram_username, parse_birth_date
from utils.tz import today as tz_today
from utils.roles import region_display_name
from utils.users import resolve_user, resolve_user_by_id

router = APIRouter(prefix="/register", tags=["register"])


async def registered_user(session, identity):
    if identity.external_identity_id is not None:
        return await resolve_user_by_id(session, identity.user_id) if identity.user_id is not None else None
    return await resolve_user(session, identity.telegram_id, identity.full_name)


async def verified_phone(session, identity):
    if identity.external_identity_id is not None:
        return identity.verified_phone
    verification = await session.get(TelegramPhoneVerification, identity.telegram_id)
    return verification.phone if verification is not None else None


def identity_condition(identity):
    return (MembershipApplication.external_identity_id == identity.external_identity_id) if identity.external_identity_id is not None else (MembershipApplication.telegram_id == identity.telegram_id)


async def contact_has_account(session, phone):
    if not phone:
        return False
    # Editable roster contacts are not identity proof: another participant
    # must not be able to reserve a victim's phone by changing their profile.
    return (await session.execute(select(User.id)
        .outerjoin(TelegramPhoneVerification, TelegramPhoneVerification.telegram_id == User.telegram_id)
        .outerjoin(ExternalIdentity, ExternalIdentity.user_id == User.id)
        .where(or_(TelegramPhoneVerification.phone == phone, ExternalIdentity.verified_phone == phone))
        .limit(1))).scalar_one_or_none() is not None


@router.get("/context")
async def register_context(
    identity: TelegramIdentity = Depends(get_telegram_identity),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Веб решает, что показывать: уже есть кабинет — сразу на него; иначе —
    список отделений для формы."""
    existing = await registered_user(session, identity)
    if existing is not None:
        return {"already_registered": True}

    regions = list(
        (await session.execute(select(Region).where(Region.is_active.is_(True)).order_by(Region.name)))
        .scalars()
        .all()
    )
    phone = await verified_phone(session, identity)
    latest = (await session.execute(select(MembershipApplication).where(identity_condition(identity))
        .order_by(MembershipApplication.id.desc()).limit(1))).scalar_one_or_none()
    return {
        "already_registered": False,
        "regions": [{"id": r.id, "label": region_display_name(r)} for r in regions],
        "verified_phone": phone,
        "provider": identity.provider,
        "manual_contact_allowed": identity.provider == "vk_mini",
        "birth_date_min": "1900-01-01",
        "birth_date_max": tz_today().isoformat(),
        "full_name": identity.full_name,
        "application": {"state": latest.state, "reason": latest.reject_reason} if latest else None,
        "existing_account": bool(identity.external_identity_id and await contact_has_account(session, phone)),
    }


@router.get("/phone")
async def register_phone(
    identity: TelegramIdentity = Depends(get_telegram_identity),
    session: AsyncSession = Depends(get_db),
) -> dict:
    phone = await verified_phone(session, identity)
    return {
        "verified": phone is not None,
        "phone": phone,
    }


def _university_dict(university: University) -> dict:
    return {"id": university.id, "name": university.name, "region_id": university.region_id}


@router.get("/universities")
async def register_universities(
    region_id: int | None = None,
    q: str = Query(default="", max_length=255),
    identity: TelegramIdentity = Depends(get_telegram_identity),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Глобальный поиск места учёбы.

    Регион участия выбирается отдельно: человек может учиться заочно или в
    другом городе и состоять в нужном ему отделении.
    """
    stmt = (
        select(University)
        .where(University.is_active.is_(True))
        .order_by(University.name)
    )
    items = list((await session.execute(stmt.limit(5_000))).scalars().all())
    needle = q.strip().lower()
    if needle:
        items = [u for u in items if needle in u.name.lower()]
    return {"items": [_university_dict(u) for u in items[:20]]}


@router.get("/cells")
async def register_cells(
    region_id: int,
    identity: TelegramIdentity = Depends(get_telegram_identity),
    session: AsyncSession = Depends(get_db),
) -> dict:
    region = await session.get(Region, region_id)
    if region is None or not region.is_active:
        raise HTTPException(400, "Выберите действующее отделение")
    rows = (await session.execute(
        select(UniversityCell)
        .where(UniversityCell.region_id == region_id, UniversityCell.is_active.is_(True))
        .order_by(UniversityCell.name)
    )).scalars().all()
    return {"items": [{"id": c.id, "name": c.name} for c in rows]}


class RegisterUniversityIn(BaseModel):
    name: str = Field(min_length=2, max_length=128)
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
    telegram_username: str | None = Field(default=None, max_length=33)
    region_id: int
    cell_id: int | None = None
    university_id: int | None = None
    university_name: str | None = Field(default=None, max_length=128)
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

    @model_validator(mode="after")
    def require_university(self) -> "RegisterSubmit":
        self.full_name = " ".join(self.full_name.split())
        self.faculty = self.faculty.strip()
        if len(self.full_name) < 2 or not self.faculty:
            raise ValueError("Укажите ФИО и факультет")
        if self.university_id is None and not (self.university_name or "").strip():
            raise ValueError("Укажите ВУЗ")
        return self


@router.post("/submit")
async def register_submit(
    payload: RegisterSubmit,
    background_tasks: BackgroundTasks,
    identity: TelegramIdentity = Depends(get_telegram_identity),
    session: AsyncSession = Depends(get_db),
) -> dict:
    existing = await registered_user(session, identity)
    if existing is not None:
        raise HTTPException(409, "У вас уже есть личный кабинет")

    region = await session.get(Region, payload.region_id)
    if region is None or not region.is_active:
        raise HTTPException(400, "Отделение не найдено")
    # Строго ДД.ММ.ГГГГ с годом — в отличие от utils/parser.py::parse_date_hint
    # (тот для быстрого ввода в чате и год не требует), у формальной анкеты
    # Маска упрощает ввод, но не заменяет серверную проверку календаря и
    # диапазона. Клиентские ограничения можно обойти прямым API-запросом.
    try:
        birth_date = parse_birth_date(payload.birth_date, tz_today())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None

    try:
        if not re.fullmatch(r"[+0-9()\s-]+", payload.phone.strip()):
            raise ValueError("Введите действующий номер телефона")
        submitted_phone = normalize_phone(payload.phone)
        telegram_username = (
            normalize_telegram_username(payload.telegram_username)
            if (payload.telegram_username or "").strip()
            else None
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    confirmed_phone = await verified_phone(session, identity)
    manual_contact = identity.provider == "vk_mini" and not confirmed_phone
    if (IS_PRODUCTION or identity.external_identity_id is not None) and not confirmed_phone and not manual_contact:
        raise HTTPException(400, "Разрешите VK ID передать телефон и повторите вход" if identity.external_identity_id else "Подтвердите номер через Telegram")
    phone = confirmed_phone or submitted_phone
    if confirmed_phone is not None and submitted_phone != confirmed_phone:
        raise HTTPException(400, "Номер изменился — подтвердите его ещё раз")
    if identity.external_identity_id is not None and confirmed_phone:
        if await contact_has_account(session, phone):
            raise HTTPException(409, "С этим телефоном уже есть кабинет. Войдите в него через Telegram и подключите VK в разделе «Способы входа»")
    elif confirmed_phone:
        web_account = (await session.execute(select(User.id).join(ExternalIdentity, ExternalIdentity.user_id == User.id)
            .where(ExternalIdentity.verified_phone == phone, User.telegram_id.is_(None)).limit(1))).scalar_one_or_none()
        if web_account is not None:
            raise HTTPException(409, "С этим телефоном уже есть веб-кабинет. Войдите в него через VK ID, не создавая вторую анкету")
    cross_provider = (MembershipApplication.phone == phone) & (MembershipApplication.state == APPLICATION_STATE_PENDING)
    cross_provider = cross_provider & or_(
        MembershipApplication.telegram_id.in_(select(TelegramPhoneVerification.telegram_id).where(TelegramPhoneVerification.phone == phone)),
        MembershipApplication.external_identity_id.in_(select(ExternalIdentity.id).where(ExternalIdentity.verified_phone == phone)),
    )
    if identity.external_identity_id is None:
        cross_provider = cross_provider & MembershipApplication.external_identity_id.is_not(None)
    # An unverified contact must neither discover nor reserve someone else's
    # phone. Only a signed provider number participates in cross-account checks.
    existing_request = (await session.execute(select(MembershipApplication.id).where(cross_provider).limit(1))).scalar_one_or_none() if confirmed_phone else None
    if existing_request is not None:
        raise HTTPException(409, "Заявка с этим телефоном уже отправлена. Дождитесь решения руководителя")

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
                identity_condition(identity),
                MembershipApplication.state == APPLICATION_STATE_PENDING,
            )
        )
    ).scalar_one_or_none()
    if pending is not None:
        raise HTTPException(409, "Анкета уже отправлена и ждёт подтверждения руководителя")

    if payload.university_id is not None:
        university = await session.get(University, payload.university_id)
        if university is None:
            raise HTTPException(400, "ВУЗ не найден")
        if not university.is_active:
            raise HTTPException(400, "Этот ВУЗ архивирован — обратитесь к руководителю отделения")
    else:
        # Неизвестный вуз создаётся только вместе с валидной заявкой. Отдельный
        # публичный POST /universities по-прежнему запрещён: так один Telegram-
        # аккаунт не сможет бесконтрольно засорять справочник без заявок.
        university_name = unicodedata.normalize("NFKC", " ".join((payload.university_name or "").split()))
        if len(university_name) < 2:
            raise HTTPException(400, "Напишите название вуза полностью")
        key = university_name.casefold()
        universities = (await session.execute(select(University))).scalars().all()
        university = next((item for item in universities if item.name.casefold() == key), None)
        if university is not None and not university.is_active:
            raise HTTPException(400, "Этот ВУЗ архивирован — обратитесь к руководителю отделения")
        if university is None:
            university = University(name=university_name, region_id=region.id)
            session.add(university)

    cell = await session.get(UniversityCell, payload.cell_id) if payload.cell_id is not None else None
    if payload.cell_id is not None and (cell is None or cell.region_id != region.id or not cell.is_active):
        raise HTTPException(400, "Выберите действующую ячейку выбранного отделения")

    application = MembershipApplication(
        region_id=region.id,
        cell_id=cell.id if cell else None,
        telegram_id=identity.telegram_id,
        external_identity_id=identity.external_identity_id,
        full_name=payload.full_name.strip(),
        phone=phone,
        telegram_username=telegram_username,
        birth_date=birth_date,
        university=university,
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
        "Проверка контакта: " + ("номер получен от провайдера" if confirmed_phone else "указан вручную; уточните перед одобрением"),
        f"Дата рождения: {birth_date.strftime('%d.%m.%Y')}",
        f"ВУЗ: {escape_telegram_html(university.name if university else '?')}",
        f"Ячейка Братства: {escape_telegram_html(cell.name if cell else 'региональное отделение')}",
        f"Факультет: {escape_telegram_html(application.faculty)}",
        f"Курс: {'окончил' if application.graduated_university else application.course}",
        f"Статус: {MEMBER_STATUS_LABELS[application.member_status]}",
    ]
    if application.telegram_username:
        summary_lines.insert(2, f"Telegram: {escape_telegram_html(application.telegram_username)}")
    if application.education_level:
        summary_lines.insert(-1, f"Уровень: {EDUCATION_LEVEL_LABELS[application.education_level]}")
    if application.workplace:
        summary_lines.append(f"Место работы: {escape_telegram_html(application.workplace)}")

    notify_text = (
        f"📝 <b>Подтверждение личного кабинета — {escape_telegram_html(region.name)}</b>\n\n"
        + "\n".join(summary_lines)
    )
    for reviewer in await _reviewers(session, region):
        if reviewer.telegram_id and reviewer.telegram_id != identity.telegram_id:
            background_tasks.add_task(notify_telegram, reviewer.telegram_id, notify_text)

    # Подтверждение в чат бота — отдельно от текста внутри самой Mini App
    # (webapp/app.js::renderRegisterView), явно попросили именно сообщение в чат.
    if identity.telegram_id is not None:
        background_tasks.add_task(notify_telegram, identity.telegram_id, "Заявка отправлена, ожидайте.")

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
        await session.execute(
            select(User).where(
                or_(User.role == ROLE_FEDERAL, User.is_federal.is_(True)),
                User.is_active.is_(True),
            )
        )
    ).scalars().first()


async def _reviewers(session: AsyncSession, region: Region) -> list[User]:
    """Все, кто должен сразу узнать о новой заявке этого региона."""
    people: dict[int, User] = {}
    if region.leader_user_id:
        leader = await session.get(User, region.leader_user_id)
        if leader is not None and leader.is_active:
            people[leader.id] = leader

    coordinators = (await session.execute(
        select(User)
        .join(CoordinatorRegion, CoordinatorRegion.coordinator_user_id == User.id)
        .where(CoordinatorRegion.region_id == region.id, User.is_active.is_(True))
    )).scalars().all()
    for person in coordinators:
        people[person.id] = person

    bureau = (await session.execute(
        select(User)
        .join(BureauMember, BureauMember.user_id == User.id)
        .join(BureauRegion, BureauRegion.bureau_member_id == BureauMember.id)
        .where(BureauRegion.region_id == region.id, User.is_active.is_(True))
    )).scalars().all()
    for person in bureau:
        people[person.id] = person

    elevated = (await session.execute(
        select(User).where(
            User.is_active.is_(True),
            or_(
                User.role.in_((ROLE_SUPERUSER, ROLE_FEDERAL)),
                User.is_superuser.is_(True),
                User.is_federal.is_(True),
            ),
        )
    )).scalars().all()
    for person in elevated:
        people[person.id] = person

    preferred = await _pick_reviewer(session, region)
    if preferred is not None and preferred.is_active:
        people[preferred.id] = preferred
    return list(people.values())
