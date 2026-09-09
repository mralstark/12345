"""Миграция: таблица shop_purchases — магазин наград за звёзды (план
«Персонаж и инвентарь»), см. api/routers/shop.py.

    python -m scripts.migrate_shop
"""

import asyncio

from sqlalchemy import inspect

from database.db import engine
from database.models import Base, ShopPurchase


async def migrate() -> None:
    async with engine.begin() as conn:
        has_table = await conn.run_sync(lambda sync_conn: inspect(sync_conn).has_table("shop_purchases"))
        if has_table:
            print("Таблица shop_purchases уже есть")
            return
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=[ShopPurchase.__table__]))
        print("Создана таблица shop_purchases")


if __name__ == "__main__":
    asyncio.run(migrate())
