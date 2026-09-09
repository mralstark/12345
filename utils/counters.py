"""Счётчики непрочитанного для главного экрана кабинета и меню бота (ТЗ §10.2)."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    APPLICATION_STATE_PENDING,
    EVENT_STATUS_CANCELLED,
    EVENT_STATUS_DONE,
    ROLE_CELL_LEADER,
    ROLE_FEDERAL,
    ROLE_LEADER,
    ROLE_SUPERUSER,
    TASK_STATUSES_OPEN,
    EventTask,
    EventTaskAssignee,
    Member,
    MembershipApplication,
    ShopPurchase,
    Task,
    User,
)
from utils.access import accessible_region_ids, actor_cell


async def new_tasks_count(session: AsyncSession, user_id: int) -> int:
    """Непросмотренные входящие задачи — то, что показывается бейджем.

    Закрытые не считаем, даже если их не открывали: законченная задача не
    может звать к себе. Иначе кружок горел вечно у того, кто отметил задачу
    выполненной, ни разу не открыв её карточку.
    """
    result = await session.execute(
        select(func.count(Task.id)).where(
            Task.to_user_id == user_id,
            Task.is_read.is_(False),
            Task.status.in_(TASK_STATUSES_OPEN),
        )
    )
    return result.scalar() or 0


async def open_tasks_count(session: AsyncSession, user_id: int) -> int:
    result = await session.execute(
        select(func.count(Task.id)).where(Task.to_user_id == user_id, Task.status.in_(TASK_STATUSES_OPEN))
    )
    return result.scalar() or 0


async def new_event_tasks_count(session: AsyncSession, member_id: int | None) -> int:
    """Непрочитанные задачи мероприятий, назначенные этому человеку. Входят в
    общий счётчик «Задач» вместе с обычными: для человека это одна и та же
    работа, и кружок у неё один (см. api/routers/context.py).

    Закрытые сюда не входят по той же причине, что и у обычных задач. У этих
    оно вдобавок вылезло на живых данных: задачи, выполненные до того, как
    отметку о прочтении вообще стали ставить, остались бы непрочитанными
    навсегда, и кружок у человека не погас бы уже никогда.
    """
    if member_id is None:
        return 0
    result = await session.execute(
        select(func.count(EventTaskAssignee.id))
        .join(EventTask, EventTask.id == EventTaskAssignee.task_id)
        .where(
            EventTaskAssignee.member_id == member_id,
            EventTaskAssignee.is_read.is_(False),
            EventTask.status.not_in((EVENT_STATUS_DONE, EVENT_STATUS_CANCELLED)),
        )
    )
    return result.scalar() or 0


async def new_purchases_count(session: AsyncSession, user: User) -> int:
    """Непросмотренные покупки в магазине среди подопечных этого руководителя —
    красный кружок на вкладке «Состав» (см. api/routers/shop.py::buy_item,
    list_member_purchases). Только для leader/cell_leader — остальным ролям
    «Состав» не принадлежит в этом смысле."""
    if user.role not in (ROLE_LEADER, ROLE_CELL_LEADER):
        return 0
    stmt = (
        select(func.count(ShopPurchase.id))
        .join(Member, Member.id == ShopPurchase.member_id)
        .where(ShopPurchase.seen_by_leader.is_(False))
    )
    cell = await actor_cell(session, user)
    if cell is not None:
        stmt = stmt.where(Member.cell_id == cell.id)
    else:
        region_ids = await accessible_region_ids(session, user)
        if not region_ids:
            return 0
        stmt = stmt.where(Member.region_id.in_(region_ids))
    result = await session.execute(stmt)
    return result.scalar() or 0


async def has_pending_application(session: AsyncSession, telegram_id: int) -> bool:
    """Своя анкета этого telegram_id ещё не рассмотрена — пока это так, бот
    не даёт ни подать вторую, ни увидеть обычные тексты «доступ не открыт»/
    приветствие (handlers/fallback.py, handlers/start.py): вместо этого
    напоминание, что решение ещё не принято."""
    result = await session.execute(
        select(MembershipApplication.id).where(
            MembershipApplication.telegram_id == telegram_id,
            MembershipApplication.state == APPLICATION_STATE_PENDING,
        )
    )
    return result.scalar_one_or_none() is not None


async def pending_applications_count(session: AsyncSession, user: User) -> int:
    """Анкеты, ждущие подтверждения — счётчик у кнопки «📝 Подтверждения»
    (handlers/apply.py). Временно (план «Убираем технического superuser») —
    подтверждают federal/superuser, не руководители регионов, поэтому считаем
    для них общее число по всей организации, а не по одному региону."""
    if user.role not in (ROLE_FEDERAL, ROLE_SUPERUSER):
        return 0
    result = await session.execute(
        select(func.count(MembershipApplication.id)).where(MembershipApplication.state == APPLICATION_STATE_PENDING)
    )
    return result.scalar() or 0
