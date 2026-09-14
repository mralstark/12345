"""Модуль «Финансы» (ТЗ §7): категории с ключевыми словами, операции,
баланс и статистика по календарным периодам, CSV-экспорт."""

from datetime import date as date_

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user, get_db
from api.serializers import category_dict, transaction_dict
from database.models import Category, Event, Keyword, Transaction, UniversityCell, User
from services.finance import create_transaction, ensure_default_categories, region_categories
from utils.access import actor_cell, require_edit, require_same_cell, require_view
from utils.balance_calc import get_balance, get_category_breakdown, get_cell_totals, get_totals
from utils.csv_export import build_transactions_csv
from utils.parser import normalize_keyword
from utils.period import resolve_period

router = APIRouter(prefix="/finance", tags=["finance"])


class TransactionIn(BaseModel):
    region_id: int
    amount: int = Field(gt=0, description="Сумма в копейках")
    type: str
    date: date_ | None = None
    category_id: int | None = None
    event_id: int | None = None
    cell_id: int | None = None
    comment: str | None = Field(default=None, max_length=255)


class TransactionPatch(BaseModel):
    amount: int | None = Field(default=None, gt=0)
    type: str | None = None
    date: date_ | None = None
    category_id: int | None = None
    event_id: int | None = None
    cell_id: int | None = None
    comment: str | None = Field(default=None, max_length=255)


class CategoryIn(BaseModel):
    region_id: int
    name: str = Field(min_length=1, max_length=64)
    type: str
    emoji: str | None = Field(default=None, max_length=8)
    keywords: list[str] = []


class CategoryPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    emoji: str | None = Field(default=None, max_length=8)
    keywords: list[str] | None = None


async def _check_category(session: AsyncSession, category_id: int | None, region_id: int) -> None:
    if category_id is None:
        return
    category = await session.get(Category, category_id)
    if category is None or category.region_id != region_id:
        raise HTTPException(400, "Категория не принадлежит этому региону")


async def _check_event(session: AsyncSession, event_id: int | None, region_id: int) -> None:
    if event_id is None:
        return
    event = await session.get(Event, event_id)
    if event is None or event.region_id != region_id:
        raise HTTPException(400, "Мероприятие не принадлежит этому региону")


async def _check_cell(session: AsyncSession, cell_id: int | None, region_id: int) -> None:
    if cell_id is None:
        return
    cell = await session.get(UniversityCell, cell_id)
    if cell is None or cell.region_id != region_id or not cell.is_active:
        raise HTTPException(400, "Ячейка не принадлежит этому региону")


@router.get("/overview")
async def overview(
    region_id: int,
    period: str = "month",
    offset: int = 0,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    await require_view(session, user, region_id)
    start, end, label = resolve_period(period, offset)
    cell = await actor_cell(session, user)

    if cell is not None:
        # Руководитель ячейки видит баланс и статистику только своей ячейки,
        # не всего региона (utils/balance_calc.get_cell_totals).
        income, expense = await get_cell_totals(session, cell.id, start, end)
        balance_income, balance_expense = await get_cell_totals(session, cell.id)
        balance = balance_income - balance_expense
    else:
        income, expense = await get_totals(session, region_id, start, end)
        balance = await get_balance(session, region_id)

    return {
        "period": {"kind": period, "offset": offset, "label": label, "start": start.isoformat(), "end": end.isoformat()},
        "balance": balance,
        "income": income,
        "expense": expense,
        "net": income - expense,
        "expense_by_category": [
            {"name": name, "emoji": emoji, "amount": amount}
            for name, emoji, amount in await get_category_breakdown(
                session, region_id, "expense", start, end, cell_id=cell.id if cell else None
            )
        ],
        "income_by_category": [
            {"name": name, "emoji": emoji, "amount": amount}
            for name, emoji, amount in await get_category_breakdown(
                session, region_id, "income", start, end, cell_id=cell.id if cell else None
            )
        ],
    }


async def _transaction_rows(
    session: AsyncSession,
    region_id: int,
    start: date_ | None,
    end: date_ | None,
    tx_type: str | None = None,
    category_id: int | None = None,
    event_id: int | None = None,
    limit: int | None = None,
    offset: int = 0,
    cell_id: int | None = None,
) -> list[tuple]:
    stmt = (
        select(Transaction, Category.name, Category.emoji, Event.title, User.full_name)
        .join(Category, Category.id == Transaction.category_id, isouter=True)
        .join(Event, Event.id == Transaction.event_id, isouter=True)
        .join(User, User.id == Transaction.author_id, isouter=True)
        .where(Transaction.region_id == region_id)
    )
    if cell_id is not None:
        stmt = stmt.where(Transaction.cell_id == cell_id)
    if start is not None:
        stmt = stmt.where(Transaction.date >= start)
    if end is not None:
        stmt = stmt.where(Transaction.date <= end)
    if tx_type:
        stmt = stmt.where(Transaction.type == tx_type)
    if category_id:
        stmt = stmt.where(Transaction.category_id == category_id)
    if event_id:
        stmt = stmt.where(Transaction.event_id == event_id)

    stmt = stmt.order_by(Transaction.date.desc(), Transaction.id.desc()).offset(offset)
    if limit is not None:
        stmt = stmt.limit(limit)

    result = await session.execute(stmt)
    return list(result.all())


@router.get("/transactions")
async def list_transactions(
    region_id: int,
    period: str = "month",
    offset: int = 0,
    type: str | None = None,
    category_id: int | None = None,
    event_id: int | None = None,
    limit: int = 100,
    skip: int = 0,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    await require_view(session, user, region_id)
    start, end, label = resolve_period(period, offset)
    cell = await actor_cell(session, user)

    rows = await _transaction_rows(
        session, region_id, start, end, type, category_id, event_id,
        limit=limit + 1, offset=skip, cell_id=cell.id if cell else None,
    )
    has_more = len(rows) > limit
    rows = rows[:limit]

    return {
        "period_label": label,
        "has_more": has_more,
        "items": [
            transaction_dict(
                tx,
                category_name=f"{emoji or ''} {name}".strip() if name else None,
                event_title=event_title,
                author_name=author,
            )
            for tx, name, emoji, event_title, author in rows
        ],
    }


@router.post("/transactions")
async def add_transaction(
    payload: TransactionIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    await require_edit(session, user, payload.region_id)
    await _check_category(session, payload.category_id, payload.region_id)
    await _check_event(session, payload.event_id, payload.region_id)

    cell = await actor_cell(session, user)
    # Сервер не доверяет cell_id от клиента, если пишет руководитель ячейки.
    cell_id = cell.id if cell is not None else payload.cell_id
    await _check_cell(session, cell_id, payload.region_id)

    transaction = await create_transaction(
        session,
        region_id=payload.region_id,
        amount_kopecks=payload.amount,
        tx_type=payload.type,
        category_id=payload.category_id,
        event_id=payload.event_id,
        cell_id=cell_id,
        author_id=user.id,
        tx_date=payload.date,
        comment=payload.comment,
    )
    return transaction_dict(transaction)


@router.patch("/transactions/{transaction_id}")
async def edit_transaction(
    transaction_id: int,
    payload: TransactionPatch,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    transaction = await session.get(Transaction, transaction_id)
    if transaction is None:
        raise HTTPException(404, "Операция не найдена")
    await require_edit(session, user, transaction.region_id)
    cell = await actor_cell(session, user)
    require_same_cell(cell, transaction.cell_id)

    data = payload.model_dump(exclude_unset=True)
    if cell is not None:
        data.pop("cell_id", None)
    if "type" in data and data["type"] not in ("income", "expense"):
        raise HTTPException(400, "Тип операции — income или expense")
    if "category_id" in data:
        await _check_category(session, data["category_id"], transaction.region_id)
    if "event_id" in data:
        await _check_event(session, data["event_id"], transaction.region_id)
    if "cell_id" in data:
        await _check_cell(session, data["cell_id"], transaction.region_id)

    for field, value in data.items():
        setattr(transaction, field, value)
    await session.commit()
    await session.refresh(transaction)
    return transaction_dict(transaction)


@router.delete("/transactions/{transaction_id}")
async def delete_transaction(
    transaction_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    transaction = await session.get(Transaction, transaction_id)
    if transaction is None:
        raise HTTPException(404, "Операция не найдена")
    await require_edit(session, user, transaction.region_id)
    require_same_cell(await actor_cell(session, user), transaction.cell_id)
    await session.delete(transaction)
    await session.commit()
    return {"ok": True}


@router.get("/categories")
async def list_categories(
    region_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    await require_view(session, user, region_id)
    await ensure_default_categories(session, region_id)

    categories = await region_categories(session, region_id)
    keywords_result = await session.execute(
        select(Keyword.category_id, Keyword.word).join(Category, Category.id == Keyword.category_id).where(
            Category.region_id == region_id
        )
    )
    by_category: dict[int, list[str]] = {}
    for category_id, word in keywords_result.all():
        by_category.setdefault(category_id, []).append(word)

    return {"items": [category_dict(c, by_category.get(c.id, [])) for c in categories]}


@router.post("/categories")
async def add_category(
    payload: CategoryIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    await require_edit(session, user, payload.region_id)
    if payload.type not in ("income", "expense"):
        raise HTTPException(400, "Тип категории — income или expense")

    category = Category(
        region_id=payload.region_id,
        name=payload.name.strip(),
        type=payload.type,
        emoji=(payload.emoji or "").strip() or None,
    )
    session.add(category)
    await session.flush()
    for word in payload.keywords:
        normalized = normalize_keyword(word)
        if normalized:
            session.add(Keyword(category_id=category.id, word=normalized))
    await session.commit()
    await session.refresh(category)
    return category_dict(category, [normalize_keyword(w) for w in payload.keywords if w.strip()])


@router.patch("/categories/{category_id}")
async def edit_category(
    category_id: int,
    payload: CategoryPatch,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    category = await session.get(Category, category_id)
    if category is None:
        raise HTTPException(404, "Категория не найдена")
    await require_edit(session, user, category.region_id)

    data = payload.model_dump(exclude_unset=True)
    if data.get("name"):
        category.name = data["name"].strip()
    if "emoji" in data:
        category.emoji = (data["emoji"] or "").strip() or None

    keywords = data.get("keywords")
    if keywords is not None:
        existing = await session.execute(select(Keyword).where(Keyword.category_id == category.id))
        for keyword in existing.scalars().all():
            await session.delete(keyword)
        await session.flush()
        for word in keywords:
            normalized = normalize_keyword(word)
            if normalized:
                session.add(Keyword(category_id=category.id, word=normalized))

    await session.commit()
    await session.refresh(category)

    words_result = await session.execute(select(Keyword.word).where(Keyword.category_id == category.id))
    return category_dict(category, list(words_result.scalars().all()))


@router.delete("/categories/{category_id}")
async def delete_category(
    category_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    category = await session.get(Category, category_id)
    if category is None:
        raise HTTPException(404, "Категория не найдена")
    await require_edit(session, user, category.region_id)

    # Операции не удаляем — они просто становятся «без категории»,
    # иначе удаление справочника молча меняло бы баланс.
    result = await session.execute(select(Transaction).where(Transaction.category_id == category.id))
    for transaction in result.scalars().all():
        transaction.category_id = None
    await session.delete(category)
    await session.commit()
    return {"ok": True}


@router.get("/export.csv")
async def export_csv(
    region_id: int,
    period: str = "year",
    offset: int = 0,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    await require_view(session, user, region_id)
    start, end, label = resolve_period(period, offset)
    cell = await actor_cell(session, user)
    rows = await _transaction_rows(session, region_id, start, end, cell_id=cell.id if cell else None)

    payload = build_transactions_csv(
        [
            (tx.date, tx.type, name, event_title, tx.amount, tx.comment, author)
            for tx, name, _emoji, event_title, author in rows
        ]
    )
    filename = f"finance_{region_id}_{start.isoformat()}_{end.isoformat()}.csv"
    return Response(
        content=payload,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
