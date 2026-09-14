"""Магазин наград за звёзды (план «Персонаж и инвентарь», MVP). Каталог
товаров захардкожен здесь (правится только в коде, аналогично character.py
::OUTFITS и quests.py — своего админ-интерфейса на этом этапе нет).

kind='outfit' — премиальный образ, после покупки сразу открывается в выборе
образа (api/routers/character.py читает ShopPurchase, чтобы понять, что ещё,
кроме бесплатных за роли, доступно). kind='physical' — материальная вещь,
которую отдают очно; тут только факт покупки/списания звёзд, статус выдачи
руководитель отмечает через fulfill_purchase ниже."""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user, get_db
from database.db import async_session
from database.models import Member, Region, ShopPurchase, UniversityCell, User
from utils.access import (
    AccessDenied,
    actor_cell,
    require_edit,
    require_same_cell,
    require_view,
)
from utils.notify import escape_telegram_html, notify_telegram

router = APIRouter(tags=["shop"])

# Покупка за звёзды закрыта на время: экономика ещё настраивается, и пока
# награды выдают руководители из рук в руки. Каталог при этом остаётся на
# виду — человеку важно знать, к чему он копит, — но кнопки «Купить» нет, и
# запрос на покупку сервер отклоняет, а не только прячет кнопку.
PURCHASES_OPEN = False
PURCHASES_CLOSED_HINT = "Покупка за звёзды пока недоступна"

SHOP_ITEMS = [
    # photos — снимки в webapp/merch/, первым каталожный, дальше на одежде.
    # Раньше у вещей за звёзды картинки не было вовсе, только подарочный
    # значок: образы показывали, а материальную награду — нет.
    {"id": "chevron", "label": "Шеврон Братства", "kind": "physical", "price": 10,
     "photos": ["chevron-1.jpg", "chevron-2.jpg", "chevron-3.jpg"],
     "description": "Тканевый шеврон с эмблемой Академистов."},
    {"id": "sticker", "label": "Наклейка на телефон", "kind": "physical", "price": 5,
     "photos": ["sticker-1.jpg", "sticker-2.jpg"],
     "description": "Объёмная наклейка с эмблемой Академистов."},
    {"id": "stolypin", "label": "Столыпин", "kind": "outfit", "price": 30, "file": "stolypin.png",
     "description": "Премиальный образ — открывает выбор в «Академии»."},
    {"id": "nicholas2", "label": "Николай II", "kind": "outfit", "price": 35, "file": "nicholas2.png",
     "description": "Премиальный образ — открывает выбор в «Академии»."},
    {"id": "alexander", "label": "Александр Македонский", "kind": "outfit", "price": 40, "file": "alexander.png",
     "description": "Премиальный образ — открывает выбор в «Академии»."},
]
SHOP_ITEMS_BY_ID = {i["id"]: i for i in SHOP_ITEMS}


async def _own_member(user: User, session: AsyncSession, *, lock: bool = False) -> Member:
    if user.member_id is None:
        raise HTTPException(404, "У этого аккаунта нет личного кабинета — он не привязан к «Составу»")
    stmt = select(Member).where(Member.id == user.member_id)
    if lock:
        stmt = stmt.with_for_update()
    member = (await session.execute(stmt)).scalar_one_or_none()
    if member is None:
        raise HTTPException(404, "Запись в составе не найдена")
    return member


async def owned_outfit_ids(session: AsyncSession, member_id: int) -> set[str]:
    """Премиальные образы, купленные человеком — использует character.py при
    сборке списка доступных образов."""
    rows = (
        await session.execute(
            select(ShopPurchase.item_id).where(ShopPurchase.member_id == member_id, ShopPurchase.kind == "outfit")
        )
    ).scalars().all()
    return set(rows)


async def _shop_payload(session: AsyncSession, member: Member) -> dict:
    owned = {
        row.item_id: row
        for row in (
            await session.execute(select(ShopPurchase).where(ShopPurchase.member_id == member.id))
        ).scalars().all()
    }
    items = [
        {
            "id": i["id"],
            "label": i["label"],
            "kind": i["kind"],
            "price": i["price"],
            "description": i["description"],
            "image": f"/static/avatars/{i['file']}" if i["kind"] == "outfit" else None,
            "photos": [f"/static/merch/{name}" for name in i.get("photos", [])],
            "owned": i["id"] in owned,
            "affordable": member.stars >= i["price"],
        }
        for i in SHOP_ITEMS
    ]
    return {
        "stars": member.stars,
        "items": items,
        "purchases_open": PURCHASES_OPEN,
        "closed_hint": PURCHASES_CLOSED_HINT,
    }


async def _send_purchase_notification(member_name: str, item_label: str, price: int, region_id: int, cell_id: int | None) -> None:
    """Фоновая задача (см. BackgroundTasks в buy_item) — своя сессия, т.к.
    сессия запроса к моменту выполнения уже закрыта (см. тот же приём в
    api/routers/event_tasks.py::_send_task_assignment_notifications)."""
    text = (
        f"🛍 <b>{escape_telegram_html(member_name)}</b> купил(а) в магазине "
        f"«{escape_telegram_html(item_label)}» за {price} ⭐"
    )
    async with async_session() as session:
        telegram_ids: set[int] = set()
        region = await session.get(Region, region_id)
        if region is not None and region.leader_user_id:
            leader = await session.get(User, region.leader_user_id)
            if leader is not None and leader.telegram_id:
                telegram_ids.add(leader.telegram_id)
        if cell_id is not None:
            cell = await session.get(UniversityCell, cell_id)
            if cell is not None and cell.leader_user_id:
                cell_leader = await session.get(User, cell.leader_user_id)
                if cell_leader is not None and cell_leader.telegram_id:
                    telegram_ids.add(cell_leader.telegram_id)
    for telegram_id in telegram_ids:
        await notify_telegram(telegram_id, text, retries=4, retry_delay=5)


@router.get("/shop")
async def get_shop(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)
) -> dict:
    member = await _own_member(user, session)
    return await _shop_payload(session, member)


@router.post("/shop/{item_id}/buy")
async def buy_item(
    item_id: str,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    if not PURCHASES_OPEN:
        raise AccessDenied(PURCHASES_CLOSED_HINT)
    # Блокировка строки сериализует параллельные покупки и получение наград:
    # баланс нельзя проверить и списать двумя запросами одновременно.
    member = await _own_member(user, session, lock=True)
    item = SHOP_ITEMS_BY_ID.get(item_id)
    if item is None:
        raise HTTPException(404, "Товар не найден")

    already = (
        await session.execute(
            select(ShopPurchase).where(ShopPurchase.member_id == member.id, ShopPurchase.item_id == item_id)
        )
    ).scalar_one_or_none()
    if already is not None:
        raise HTTPException(400, "Уже куплено")
    if member.stars < item["price"]:
        raise HTTPException(400, "Недостаточно звёзд")

    member.stars -= item["price"]
    session.add(ShopPurchase(member_id=member.id, item_id=item_id, kind=item["kind"], price_stars=item["price"]))
    await session.commit()
    background_tasks.add_task(
        _send_purchase_notification, member.full_name, item["label"], item["price"], member.region_id, member.cell_id
    )
    return await _shop_payload(session, member)


# --- Сторона руководителя: что купил человек и что ещё нужно выдать очно ---

@router.get("/members/{member_id}/shop-purchases")
async def list_member_purchases(
    member_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    member = await session.get(Member, member_id)
    if member is None:
        raise HTTPException(404, "Человек не найден")
    await require_view(session, user, member.region_id)

    rows = (
        await session.execute(
            select(ShopPurchase).where(ShopPurchase.member_id == member_id).order_by(ShopPurchase.created_at.desc())
        )
    ).scalars().all()
    # Открыл список покупок — красный кружок на «Составе» снят (см.
    # utils/counters.py::new_purchases_count).
    await session.execute(
        update(ShopPurchase).where(ShopPurchase.member_id == member_id, ShopPurchase.seen_by_leader.is_(False)).values(seen_by_leader=True)
    )
    await session.commit()
    return {
        "items": [
            {
                "id": row.id,
                "item_id": row.item_id,
                "label": SHOP_ITEMS_BY_ID.get(row.item_id, {}).get("label", row.item_id),
                "kind": row.kind,
                "price_stars": row.price_stars,
                "fulfilled": row.fulfilled,
            }
            for row in rows
        ]
    }


@router.post("/members/{member_id}/shop-purchases/{purchase_id}/fulfill")
async def fulfill_purchase(
    member_id: int,
    purchase_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    member = await session.get(Member, member_id)
    if member is None:
        raise HTTPException(404, "Человек не найден")
    await require_edit(session, user, member.region_id)
    require_same_cell(await actor_cell(session, user), member.cell_id)

    purchase = await session.get(ShopPurchase, purchase_id)
    if purchase is None or purchase.member_id != member_id:
        raise HTTPException(404, "Покупка не найдена")
    if purchase.kind != "physical":
        raise HTTPException(400, "У цифровых наград нет отдельной выдачи")

    purchase.fulfilled = True
    await session.commit()
    return {"fulfilled": True}
