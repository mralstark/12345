"""Модуль «Состав» (ТЗ §6): список, поиск, фильтр по статусу, CRUD руководителем."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user, get_db
from api.serializers import member_dict
from database.models import (
    MEMBER_STATUS_ALUMNI,
    MEMBER_STATUS_LABELS,
    MEMBER_STATUS_MEMBER,
    Member,
    ShopPurchase,
    University,
    User,
)
from services.admin_actions import exclude_member
from services.education import academic_year
from utils.access import (
    accessible_region_ids,
    actor_cell,
    require_edit,
    require_same_cell,
    require_view,
)
from utils.parser import normalize_telegram_username
from utils.tz import today as tz_today
from utils.university_cells import resolve_member_cell

router = APIRouter(prefix="/members", tags=["members"])


class MemberIn(BaseModel):
    region_id: int
    # cell_id больше не принимается от клиента — ячейка целиком производная
    # от university_id (utils/university_cells.resolve_member_cell).
    full_name: str = Field(min_length=2, max_length=128)
    phone: str | None = Field(default=None, max_length=32)
    telegram_username: str | None = Field(default=None, max_length=33)
    status: str = "activist"
    birth_date: date | None = None
    activist_joined_at: date | None = None
    member_inducted_at: date | None = None
    alumni_graduated_at: date | None = None
    comment: str | None = Field(default=None, max_length=255)
    university_id: int | None = None
    faculty: str | None = Field(default=None, max_length=255)
    # 1-6 либо «Окончил» (graduated_university=True, курс тогда пустой).
    course: int | None = Field(default=None, ge=1, le=6)
    graduated_university: bool = False
    education_level: str | None = Field(default=None, max_length=32)
    # Длительность программы не вводится руками — всегда 4 (see plan §4).
    study_years: int = Field(default=4, ge=1, le=10)
    workplace: str | None = Field(default=None, max_length=255)


class MemberPatch(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=128)
    phone: str | None = Field(default=None, max_length=32)
    telegram_username: str | None = Field(default=None, max_length=33)
    status: str | None = None
    birth_date: date | None = None
    activist_joined_at: date | None = None
    member_inducted_at: date | None = None
    alumni_graduated_at: date | None = None
    comment: str | None = Field(default=None, max_length=255)
    university_id: int | None = None
    faculty: str | None = Field(default=None, max_length=255)
    course: int | None = Field(default=None, ge=1, le=6)
    graduated_university: bool | None = None
    education_level: str | None = Field(default=None, max_length=32)
    study_years: int | None = Field(default=None, ge=1, le=10)
    workplace: str | None = Field(default=None, max_length=255)


def _validate_status(status: str) -> str:
    if status not in MEMBER_STATUS_LABELS:
        raise HTTPException(400, f"Неизвестный статус: {status}")
    return status


def _normalize_telegram_username(raw: str | None) -> str | None:
    if not raw or not raw.strip():
        return None
    try:
        return normalize_telegram_username(raw)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _require_milestone_dates(status: str, member_inducted_at: date | None, alumni_graduated_at: date | None) -> None:
    """Присваивая статус «член Братства»/«выпускник», руководитель обязан
    заполнить соответствующую веху — «Посвящение в Братство» / «Выпуск из
    студенческого Братства» (более ранняя дата активиста не обязательна:
    её можно не знать точно или дозаполнить позже)."""
    if status == MEMBER_STATUS_MEMBER and member_inducted_at is None:
        raise HTTPException(400, "Укажите дату посвящения в Братство")
    if status == MEMBER_STATUS_ALUMNI and alumni_graduated_at is None:
        raise HTTPException(400, "Укажите дату выпуска из студенческого Братства")


def _require_workplace_if_graduated(graduated_university: bool, workplace: str | None) -> None:
    """«Окончил» в выборе курса — не просто пустой курс, а осознанная
    отметка: человек больше не студент. Место работы становится обязательным
    (webapp/app.js повторяет эту же проверку на клиенте)."""
    if graduated_university and not (workplace or "").strip():
        raise HTTPException(400, "Укажите место работы")


async def _university_name(session: AsyncSession, university_id: int | None) -> str | None:
    if university_id is None:
        return None
    university = await session.get(University, university_id)
    return university.name if university is not None else None


async def _validate_university(
    session: AsyncSession, region_id: int, university_id: int | None
) -> None:
    if university_id is None:
        return
    university = await session.get(University, university_id)
    if university is None or university.region_id != region_id or not university.is_active:
        raise HTTPException(400, "Выберите действующий ВУЗ вашего региона")


@router.get("/search")
async def search_members(
    q: str = Query(min_length=1, max_length=128),
    region_id: int | None = None,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Пикер «выберите человека из состава» для бота (выдача личного кабинета,
    назначение руководителем — services/admin_actions.py) — по ФИО, в
    границах доступных актору регионов. Без region_id ищет по всем сразу
    (нужно федеральному/координатору с несколькими регионами)."""
    region_ids = await accessible_region_ids(session, user)
    if region_id is not None:
        if region_id not in region_ids:
            raise HTTPException(403, "Регион недоступен для этой роли")
        region_ids = [region_id]
    if not region_ids:
        return {"items": []}

    needle = q.strip().lower()
    if len(needle) < 2:
        return {"items": []}

    stmt = select(Member).where(Member.region_id.in_(region_ids), Member.is_active.is_(True))
    cell = await actor_cell(session, user)
    if cell is not None:
        stmt = stmt.where(Member.cell_id == cell.id)
    rows = list((await session.execute(stmt.order_by(Member.full_name).limit(200))).scalars().all())
    matches = [m for m in rows if needle in m.full_name.lower()][:15]

    existing_ids = {
        row[0]
        for row in (
            await session.execute(select(User.member_id).where(User.member_id.in_([m.id for m in matches])))
        ).all()
    }
    return {
        "items": [
            {"id": m.id, "full_name": m.full_name, "region_id": m.region_id, "has_account": m.id in existing_ids}
            for m in matches
        ]
    }


@router.get("")
async def list_members(
    region_id: int,
    q: str | None = Query(default=None, max_length=128),
    status: str | None = Query(default=None, max_length=16),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    await require_view(session, user, region_id)

    stmt = (
        select(Member, University.name)
        .outerjoin(University, University.id == Member.university_id)
        .where(Member.region_id == region_id, Member.is_active.is_(True))
    )
    cell = await actor_cell(session, user)
    if cell is not None:
        # Руководитель ячейки видит только свою ячейку, не весь регион.
        stmt = stmt.where(Member.cell_id == cell.id)
    if status:
        stmt = stmt.where(Member.status == _validate_status(status))

    result = await session.execute(stmt.order_by(Member.full_name).limit(1_000))
    rows = list(result.all())

    if q:
        # Регистронезависимый поиск считаем в Python: встроенный lower() в SQLite
        # работает только с латиницей, и «Смирнов» по запросу «смирнов» не нашёлся бы.
        needle = q.strip().lower()
        rows = [(m, u) for m, u in rows if needle in m.full_name.lower()]

    counts_stmt = select(Member.status, func.count(Member.id)).where(
        Member.region_id == region_id, Member.is_active.is_(True)
    )
    if cell is not None:
        counts_stmt = counts_stmt.where(Member.cell_id == cell.id)
    counts_result = await session.execute(counts_stmt.group_by(Member.status))
    counts = {key: value for key, value in counts_result.all()}

    # Красный кружок у конкретного человека и его «Покупок» (см.
    # utils/counters.py::new_purchases_count — тот же признак, тут просто
    # разложен по каждому, а не одной суммой на вкладку).
    unseen_ids: set[int] = set()
    if rows:
        unseen_result = await session.execute(
            select(ShopPurchase.member_id)
            .where(ShopPurchase.member_id.in_([m.id for m, _ in rows]), ShopPurchase.seen_by_leader.is_(False))
            .distinct()
        )
        unseen_ids = set(unseen_result.scalars().all())

    return {
        "items": [member_dict(m, university_name=u, has_unseen_purchases=m.id in unseen_ids) for m, u in rows],
        "counts": [
            {"status": key, "label": label, "count": counts.get(key, 0)}
            for key, label in MEMBER_STATUS_LABELS.items()
        ],
        "total": sum(counts.values()),
    }


@router.post("")
async def create_member(
    payload: MemberIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    await require_edit(session, user, payload.region_id)
    _validate_status(payload.status)
    _require_milestone_dates(payload.status, payload.member_inducted_at, payload.alumni_graduated_at)
    _require_workplace_if_graduated(payload.graduated_university, payload.workplace)

    cell = await actor_cell(session, user)
    if cell is not None:
        # Руководитель ячейки: человек всегда в его ячейке и его вузе —
        # сервер не доверяет тому, что прислал клиент (иначе той же формой
        # можно было бы создать человека «в чужой ячейке/вузе»).
        cell_id = cell.id
        university_id = cell.university_id
    else:
        university_id = payload.university_id
        await _validate_university(session, payload.region_id, university_id)
        resolved_cell = await resolve_member_cell(session, payload.region_id, university_id)
        cell_id = resolved_cell.id if resolved_cell is not None else None

    member = Member(
        region_id=payload.region_id,
        cell_id=cell_id,
        full_name=payload.full_name.strip(),
        phone=(payload.phone or "").strip() or None,
        telegram_username=_normalize_telegram_username(payload.telegram_username),
        status=payload.status,
        birth_date=payload.birth_date,
        activist_joined_at=payload.activist_joined_at,
        member_inducted_at=payload.member_inducted_at,
        alumni_graduated_at=payload.alumni_graduated_at,
        comment=(payload.comment or "").strip() or None,
        university_id=university_id,
        faculty=(payload.faculty or "").strip() or None,
        course=payload.course,
        graduated_university=payload.graduated_university,
        # Окончил — уровень обучения больше не актуален, даже если пришёл
        # в теле запроса (устаревший клиент и т.п.) — та же логика, что и
        # в api/routers/register.py.
        education_level=None if payload.graduated_university else payload.education_level,
        study_years=payload.study_years,
        workplace=(payload.workplace or "").strip() or None,
        # Иначе ближайший цикл автообновления курса (services/education.py)
        # тут же «повысит» свежедобавленного студента, не дожидаясь 1 сентября.
        course_bumped_year=academic_year(tz_today()) if payload.course is not None else None,
    )
    session.add(member)
    await session.commit()
    await session.refresh(member)
    return member_dict(member, university_name=await _university_name(session, member.university_id))


@router.patch("/{member_id}")
async def update_member(
    member_id: int,
    payload: MemberPatch,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    member = await session.get(Member, member_id)
    if member is None:
        raise HTTPException(404, "Человек не найден")
    await require_edit(session, user, member.region_id)
    cell = await actor_cell(session, user)
    require_same_cell(cell, member.cell_id)

    data = payload.model_dump(exclude_unset=True)
    if cell is not None:
        # Не даём руководителю ячейки перевесить человека на другой вуз —
        # он правит только свою ячейку.
        data.pop("university_id", None)
    elif "university_id" in data:
        await _validate_university(session, member.region_id, data["university_id"])
        resolved_cell = await resolve_member_cell(session, member.region_id, data["university_id"])
        data["cell_id"] = resolved_cell.id if resolved_cell is not None else None
    if "status" in data and data["status"] is not None:
        _validate_status(data["status"])
    if "telegram_username" in data:
        data["telegram_username"] = _normalize_telegram_username(data["telegram_username"])
    final_status = data.get("status", member.status)
    final_member_inducted_at = data.get("member_inducted_at", member.member_inducted_at)
    final_alumni_graduated_at = data.get("alumni_graduated_at", member.alumni_graduated_at)
    _require_milestone_dates(final_status, final_member_inducted_at, final_alumni_graduated_at)
    final_graduated_university = data.get("graduated_university", member.graduated_university)
    final_workplace = data.get("workplace", member.workplace)
    _require_workplace_if_graduated(final_graduated_university, final_workplace)
    if final_graduated_university:
        data["education_level"] = None
    for field, value in data.items():
        if isinstance(value, str):
            value = value.strip() or None
        setattr(member, field, value)

    if "course" in data and data["course"] is not None:
        # Курс проставлен/исправлен рукой — считаем его актуальным на этот
        # учебный год, иначе ближайший автобамп (или следующее 1 сентября,
        # если сегодня уже после него) снова сдвинет курс.
        member.course_bumped_year = academic_year(tz_today())

    await session.commit()
    await session.refresh(member)
    return member_dict(member, university_name=await _university_name(session, member.university_id))


@router.delete("/{member_id}")
async def delete_member(
    member_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """«Исключить» — полное и необратимое удаление (план §5): один и тот же
    результат для человека без личного кабинета и с ним, не мягкое скрытие."""
    member = await session.get(Member, member_id)
    if member is None:
        raise HTTPException(404, "Человек не найден")
    await require_edit(session, user, member.region_id)
    require_same_cell(await actor_cell(session, user), member.cell_id)

    await exclude_member(session, member_id)
    return {"ok": True}
