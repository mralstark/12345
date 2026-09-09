"""Финансовые агрегаты по региону. Баланс принадлежит региону (ТЗ §7)."""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Category, Transaction


async def get_totals(
    session: AsyncSession,
    region_id: int,
    start: date | None = None,
    end: date | None = None,
) -> tuple[int, int]:
    """(доходы, расходы) в копейках за период. Без дат — за всё время."""
    stmt = select(Transaction.type, func.sum(Transaction.amount)).where(Transaction.region_id == region_id)
    if start is not None:
        stmt = stmt.where(Transaction.date >= start)
    if end is not None:
        stmt = stmt.where(Transaction.date <= end)

    result = await session.execute(stmt.group_by(Transaction.type))
    totals = {"income": 0, "expense": 0}
    for tx_type, total in result.all():
        totals[tx_type] = total or 0
    return totals["income"], totals["expense"]


async def get_balance(session: AsyncSession, region_id: int) -> int:
    """Текущий баланс региона за всё время — то, что показывается на главном экране."""
    income, expense = await get_totals(session, region_id)
    return income - expense


async def get_category_breakdown(
    session: AsyncSession,
    region_id: int,
    tx_type: str,
    start: date | None = None,
    end: date | None = None,
    cell_id: int | None = None,
) -> list[tuple[str, str | None, int]]:
    """Разбивка по категориям: (название, эмодзи, сумма) по убыванию суммы.
    Операции без категории собираются в строку «Без категории». cell_id сужает
    до операций одной вузовской ячейки (для руководителя ячейки) — категории
    остаются общими на регион, сужается только сам список операций."""
    stmt = (
        select(Category.name, Category.emoji, func.sum(Transaction.amount))
        .join(Category, Category.id == Transaction.category_id, isouter=True)
        .where(Transaction.region_id == region_id, Transaction.type == tx_type)
    )
    if start is not None:
        stmt = stmt.where(Transaction.date >= start)
    if end is not None:
        stmt = stmt.where(Transaction.date <= end)
    if cell_id is not None:
        stmt = stmt.where(Transaction.cell_id == cell_id)

    result = await session.execute(stmt.group_by(Category.name, Category.emoji).order_by(func.sum(Transaction.amount).desc()))
    return [(name or "Без категории", emoji, total or 0) for name, emoji, total in result.all()]


async def get_event_fact(session: AsyncSession, event_id: int) -> int:
    """Фактические расходы по мероприятию — сумма помеченных им операций-расходов.
    Именно это сравнивается с планируемым бюджетом в карточке (ТЗ §8)."""
    result = await session.execute(
        select(func.sum(Transaction.amount)).where(
            Transaction.event_id == event_id, Transaction.type == "expense"
        )
    )
    return result.scalar() or 0


async def get_event_income(session: AsyncSession, event_id: int) -> int:
    result = await session.execute(
        select(func.sum(Transaction.amount)).where(
            Transaction.event_id == event_id, Transaction.type == "income"
        )
    )
    return result.scalar() or 0


async def get_cell_totals(
    session: AsyncSession,
    cell_id: int,
    start: date | None = None,
    end: date | None = None,
) -> tuple[int, int]:
    """(доходы, расходы) в копейках по операциям, помеченным вузовской ячейкой.
    Без дат — за всё время: это и используется для плана/факта бюджета ячейки
    (UniversityCell.allocated_budget против расхода отсюда — тем же приёмом,
    что и у мероприятия, get_event_fact). С датами — для вкладки «Финансы»
    руководителя ячейки, где нужен выбор периода, как и у региона целиком."""
    stmt = select(Transaction.type, func.sum(Transaction.amount)).where(Transaction.cell_id == cell_id)
    if start is not None:
        stmt = stmt.where(Transaction.date >= start)
    if end is not None:
        stmt = stmt.where(Transaction.date <= end)
    result = await session.execute(stmt.group_by(Transaction.type))
    totals = {"income": 0, "expense": 0}
    for tx_type, total in result.all():
        totals[tx_type] = total or 0
    return totals["income"], totals["expense"]
