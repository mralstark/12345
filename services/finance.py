"""Финансы региона: категории, ключевые слова, операции (ТЗ §7)."""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.defaults import DEFAULT_CATEGORIES
from database.models import Category, Keyword, Transaction
from utils.parser import normalize_keyword
from utils.tz import today


async def ensure_default_categories(session: AsyncSession, region_id: int) -> None:
    """Наполняет справочник категорий региона при первом обращении.
    Дальше руководитель правит его сам — повторно ничего не досоздаётся."""
    existing = await session.execute(select(func.count(Category.id)).where(Category.region_id == region_id))
    if (existing.scalar() or 0) > 0:
        return

    for name, type_, emoji, words in DEFAULT_CATEGORIES:
        category = Category(region_id=region_id, name=name, type=type_, emoji=emoji)
        session.add(category)
        await session.flush()
        for word in words:
            session.add(Keyword(category_id=category.id, word=normalize_keyword(word)))
    await session.commit()


async def find_category_by_hint(session: AsyncSession, region_id: int, hint: str) -> Category | None:
    """Ищет категорию по подсказке из быстрого ввода: сперва точное совпадение
    с ключевым словом, затем по названию категории, затем по вхождению."""
    normalized = normalize_keyword(hint)
    if not normalized:
        return None

    result = await session.execute(
        select(Category)
        .join(Keyword, Keyword.category_id == Category.id)
        .where(Category.region_id == region_id, Keyword.word == normalized)
        .limit(1)
    )
    category = result.scalar_one_or_none()
    if category:
        return category

    # Сверку с названием категории делаем в Python: lower() в SQLite не знает кириллицы.
    result = await session.execute(select(Category).where(Category.region_id == region_id))
    categories = list(result.scalars().all())
    for candidate in categories:
        if candidate.name.lower() == normalized:
            return candidate

    # Поиск по вхождению: «аренда зала для форума» → ключевое слово «аренда зала».
    # Считаем в Python, а не в SQL — ключевых слов у региона десятки, зато не нужно
    # разбираться, чем instr() в SQLite отличается от strpos() в Postgres.
    result = await session.execute(
        select(Keyword.word, Category)
        .join(Category, Category.id == Keyword.category_id)
        .where(Category.region_id == region_id)
    )
    best: tuple[int, Category] | None = None
    for word, category in result.all():
        if word and word in normalized and (best is None or len(word) > best[0]):
            best = (len(word), category)
    return best[1] if best else None


async def create_transaction(
    session: AsyncSession,
    region_id: int,
    amount_kopecks: int,
    tx_type: str,
    category_id: int | None = None,
    event_id: int | None = None,
    cell_id: int | None = None,
    author_id: int | None = None,
    tx_date: date | None = None,
    comment: str | None = None,
) -> Transaction:
    if amount_kopecks <= 0:
        raise ValueError("Сумма должна быть положительной")
    if tx_type not in ("income", "expense"):
        raise ValueError("Тип операции — income или expense")

    transaction = Transaction(
        region_id=region_id,
        amount=amount_kopecks,
        type=tx_type,
        category_id=category_id,
        event_id=event_id,
        cell_id=cell_id,
        author_id=author_id,
        date=tx_date or today(),
        comment=(comment or "").strip()[:255] or None,
    )
    session.add(transaction)
    await session.commit()
    await session.refresh(transaction)
    return transaction


async def region_categories(session: AsyncSession, region_id: int, type_: str | None = None) -> list[Category]:
    stmt = select(Category).where(Category.region_id == region_id)
    if type_:
        stmt = stmt.where(Category.type == type_)
    result = await session.execute(stmt.order_by(Category.type, Category.name))
    return list(result.scalars().all())
