"""Кто я, какие регионы доступны, сводка главного экрана (ТЗ §5)."""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from services.academy import can_manage_academy
from services.navigation import mark_seen, unseen_count
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user, get_db
from api.routers.character import BRANCHES
from api.serializers import event_dict, region_brief
from database.defaults import DOCUMENT_TYPES
from database.models import (
    APPLICATION_STATE_PENDING,
    EVENT_STATUS_PLANNED,
    MEMBER_STATUS_LABELS,
    MEMBER_STATUS_ACTIVIST,
    MemberQuestProgress,
    Quest,
    Task,
    SectionRead,
    ROLE_SUPERUSER,
    SUPERVISOR_ROLES,
    Event,
    Member,
    MembershipApplication,
    Region,
    User,
)
from services.news import can_post_news
from utils.access import accessible_regions, actor_cell, can_edit_region, require_view
from utils.balance_calc import get_balance, get_cell_totals, get_totals
from utils.counters import (
    new_event_tasks_count,
    new_purchases_count,
    new_tasks_count,
    open_tasks_count,
    pending_applications_count,
)
from utils.period import resolve_period
from utils.roles import role_label
from utils.permissions import has_any_role, has_role, role_codes
from utils.tz import today as tz_today
from utils.users import stop_impersonation

router = APIRouter(tags=["context"])


async def _region_or_cell_balance(session: AsyncSession, region_id: int, cell) -> int:
    """Баланс своей ячейки для её руководителя, иначе — всего региона."""
    if cell is None:
        return await get_balance(session, region_id)
    income, expense = await get_cell_totals(session, cell.id)
    return income - expense


@router.get("/me")
async def me(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)) -> dict:
    regions = await accessible_regions(session, user)
    editable = [r.id for r in regions if await can_edit_region(session, user, r.id)]
    # Если это superuser в режиме «войти как» — user уже подставлен целью
    # (utils.users.resolve_user), реальная личность лежит в этом атрибуте.
    impersonated_by = getattr(user, "_impersonated_by", None)
    # Статус (активист/член Братства/выпускник) — для шапки личного кабинета
    # сразу при входе (webapp/app.js::renderRegionSelect), без похода за
    # /profile/me: та же подпись, что renderProfile выставляет сама, просто
    # доступна с первого экрана («Академия»), не только после «Личной информации».
    my_cell = await actor_cell(session, user)
    personal_status_label = None
    member = None
    if user.member_id is not None:
        member = await session.get(Member, user.member_id)
        if member is not None:
            personal_status_label = MEMBER_STATUS_LABELS.get(member.status, member.status)
    from api.routers.character import _quest_dict
    progress = [] if member is None else (await session.execute(select(MemberQuestProgress, Quest).join(
        Quest, Quest.id == MemberQuestProgress.quest_id,
    ).where(MemberQuestProgress.member_id == member.id))).all()
    academy_alerts = sum(int(bool(q.is_active and row.assigned_by_user_id and not row.pending_count)) + int(
        _quest_dict(q, row.count, row.stars_claimed, earned_stars_floor=row.earned_stars_floor or 0)["claimable_stars"] > 0
    ) for row, q in progress)
    ids = [r.id for r in regions]
    pending_by_region = {}
    if ids and can_manage_academy(user):
        pending_by_region = dict((await session.execute(select(Member.region_id, func.count(MemberQuestProgress.id))
            .select_from(MemberQuestProgress).join(Member).join(Quest).where(
                Member.region_id.in_(ids), Member.is_active.is_(True), Member.status == MEMBER_STATUS_ACTIVIST,
                Quest.is_active.is_(True), MemberQuestProgress.pending_count > 0,
            ).group_by(Member.region_id))).all())
    event_stmt = select(Event.region_id, func.count(Event.id)).outerjoin(SectionRead, and_(
        SectionRead.user_id == user.id, SectionRead.section == "events", SectionRead.scope_id == Event.region_id,
    )).where(Event.region_id.in_(ids), Event.date >= tz_today(), Event.status == EVENT_STATUS_PLANNED,
             Event.id > func.coalesce(SectionRead.last_item_id, 0))
    if my_cell is not None:
        event_stmt = event_stmt.where(Event.cell_id == my_cell.id)
    events_by_region = dict((await session.execute(event_stmt.group_by(Event.region_id))).all()) if ids else {}
    region_counters = {str(r.id): {"academy": pending_by_region.get(r.id, 0), "events": events_by_region.get(r.id, 0)} for r in regions}
    reviews = (await session.execute(select(func.count(Task.id)).where(
        Task.from_user_id == user.id, Task.status == "review", Task.review_is_read.is_(False),
    ))).scalar() or 0
    incoming = await new_tasks_count(session, user.id) + await new_event_tasks_count(session, user.member_id)
    return {
        "id": user.id,
        "full_name": user.full_name,
        "role": user.role,
        "roles": role_codes(user),
        "role_label": await role_label(session, user),
        "is_supervisor": has_any_role(user, SUPERVISOR_ROLES),
        "regions": [region_brief(r) for r in regions],
        # Руководитель вузовской ячейки: его кабинет — не регион целиком, а
        # одна ячейка внутри него (utils/access.py::actor_cell). Переключатель
        # кабинетов подписывал его регионом, и «Санкт-Петербург» обещал
        # больше, чем кабинет показывает: людей всего города там нет.
        "cell": (
            {"id": my_cell.id, "name": my_cell.name, "region_id": my_cell.region_id}
            if my_cell is not None
            else None
        ),
        "editable_region_ids": editable,
        "impersonated_by": impersonated_by.full_name if impersonated_by else None,
        # Есть ли личный кабинет (вкладки «Личная информация»/«Новости») —
        # веб решает по этому полю, показывать ли переключатель кабинетов.
        "has_personal_cabinet": user.member_id is not None,
        "personal_status_label": personal_status_label,
        "academy_enabled": bool(member and member.is_active and member.status == MEMBER_STATUS_ACTIVIST),
        "can_manage_academy": can_manage_academy(user),
        # Подпись и область рассылки проверяются сервером по доступным регионам.
        "can_post_news": can_post_news(user),
        "counters": {
            "personal_academy": academy_alerts,
            "news": await unseen_count(session, user, "news"),
            "personal_events": await unseen_count(session, user, "events") if member and member.is_active else 0,
            "regions": region_counters,
            "management_tasks": reviews + (incoming if user.member_id is None else 0),
            # Одна цифра на всю работу: задачи мероприятий теперь приходят
            # в тот же ящик, что и обычные, — значит и кружок у них общий.
            "new_tasks": incoming,
            "open_tasks": await open_tasks_count(session, user.id),
            "new_purchases": await new_purchases_count(session, user),
            # Вкладка «Заявки» пока только у superuser (api/routers/applications.py) —
            # не считаем лишний раз для остальных ролей.
            "pending_applications": await pending_applications_count(session, user),
        },
        "dictionaries": {
            "member_statuses": [{"value": k, "label": v} for k, v in MEMBER_STATUS_LABELS.items()],
            "document_types": DOCUMENT_TYPES,
            # Категории задач мероприятия (api/routers/event_tasks.py) — те
            # же роли, что и в Академии (api/routers/character.py::BRANCHES).
            "quest_branches": [{"value": b["id"], "label": b["label"]} for b in BRANCHES],
        },
    }


@router.post("/me/stop-impersonation")
async def stop_impersonation_endpoint(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)
) -> dict:
    """Кнопка «Выйти» в баннере веб-кабинета — тот же выход, что и в боте
    (utils.users.stop_impersonation), просто с другой стороны интерфейса."""
    impersonated_by = getattr(user, "_impersonated_by", None)
    if impersonated_by is None:
        raise HTTPException(400, "Режим «войти как» сейчас не активен")
    # impersonated_by загружен в сессии get_current_user, которая уже закрыта —
    # коммит через session (другую, из Depends(get_db)) её изменений не увидит,
    # поэтому перезапрашиваем ту же строку заново в текущей сессии.
    real = await session.get(User, impersonated_by.id)
    if real is None:
        raise HTTPException(404, "Пользователь не найден")
    await stop_impersonation(session, real)
    return {"ok": True}


@router.get("/dashboard")
async def dashboard(
    region_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Главный экран кабинета руководителя: состав по статусам, баланс,
    ближайшие мероприятия (ТЗ §5)."""
    await require_view(session, user, region_id)

    region = await session.get(Region, region_id)
    if region is None:
        raise HTTPException(404, "Регион не найден")
    cell = await actor_cell(session, user)

    status_stmt = select(Member.status, func.count(Member.id)).where(
        Member.region_id == region_id, Member.is_active.is_(True)
    )
    if cell is not None:
        status_stmt = status_stmt.where(Member.cell_id == cell.id)
    status_rows = await session.execute(status_stmt.group_by(Member.status))
    by_status = {status: count for status, count in status_rows.all()}

    today = tz_today()
    month_start, month_end, month_label = resolve_period("month", 0, today)
    if cell is not None:
        month_income, month_expense = await get_cell_totals(session, cell.id, month_start, month_end)
    else:
        month_income, month_expense = await get_totals(session, region_id, month_start, month_end)

    new_members_stmt = select(func.count(Member.id)).where(
        Member.region_id == region_id,
        Member.is_active.is_(True),
        Member.activist_joined_at.is_not(None),
        Member.activist_joined_at >= month_start,
        Member.activist_joined_at <= month_end,
    )
    if cell is not None:
        new_members_stmt = new_members_stmt.where(Member.cell_id == cell.id)
    new_members = (await session.execute(new_members_stmt)).scalar() or 0

    upcoming_stmt = (
        select(Event, Member.full_name)
        .join(Member, Member.id == Event.responsible_member_id, isouter=True)
        .where(
            Event.region_id == region_id,
            Event.date >= today,
            Event.status == EVENT_STATUS_PLANNED,
        )
    )
    if cell is not None:
        upcoming_stmt = upcoming_stmt.where(Event.cell_id == cell.id)
    upcoming_rows = await session.execute(upcoming_stmt.order_by(Event.date).limit(5))
    upcoming = [event_dict(event, responsible_name=name) for event, name in upcoming_rows.all()]

    birthday_stmt = select(Member).where(
        Member.region_id == region_id, Member.is_active.is_(True), Member.birth_date.is_not(None)
    )
    if cell is not None:
        birthday_stmt = birthday_stmt.where(Member.cell_id == cell.id)
    birthday_rows = await session.execute(birthday_stmt)
    horizon = today + timedelta(days=14)
    birthdays = []
    for member in birthday_rows.scalars().all():
        for year in (today.year, today.year + 1):
            try:
                celebration = member.birth_date.replace(year=year)
            except ValueError:  # 29 февраля в невисокосный год
                celebration = member.birth_date.replace(year=year, day=28)
            if today <= celebration <= horizon:
                birthdays.append(
                    {
                        "id": member.id,
                        "full_name": member.full_name,
                        "date": celebration.isoformat(),
                        "turns": year - member.birth_date.year,
                    }
                )
                break
    birthdays.sort(key=lambda item: item["date"])

    return {
        "region": region_brief(region),
        "can_edit": await can_edit_region(session, user, region_id),
        "members": {
            "total": sum(by_status.values()),
            "by_status": [
                {"status": key, "label": label, "count": by_status.get(key, 0)}
                for key, label in MEMBER_STATUS_LABELS.items()
            ],
            "new_this_month": new_members,
        },
        "finance": {
            "balance": await _region_or_cell_balance(session, region_id, cell),
            "period_label": month_label,
            "period_income": month_income,
            "period_expense": month_expense,
        },
        "upcoming_events": upcoming,
        "birthdays": birthdays,
        "counters": {
            "new_tasks": ((await session.execute(select(func.count(Task.id)).where(Task.from_user_id == user.id, Task.status == "review", Task.review_is_read.is_(False)))).scalar() or 0) + (await new_tasks_count(session, user.id) if user.member_id is None else 0),
            "open_tasks": await open_tasks_count(session, user.id),
        },
    }


class SeenIn(BaseModel):
    section: str = Field(max_length=16)
    cursor: int = Field(ge=0)
    region_id: int | None = None


@router.post("/me/seen")
async def seen(payload: SeenIn, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)):
    await mark_seen(session, user, payload.section, payload.cursor, payload.region_id)
    return {"ok": True}
