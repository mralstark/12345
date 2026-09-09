"""Миграция: events.time (точное время мероприятия — для напоминания за 24
часа, см. services/notifier.py::check_event_reminders) и
shop_purchases.seen_by_leader (красный кружок у руководителя на вкладке
«Состав» — см. api/routers/shop.py).

    python -m scripts.migrate_event_time_and_purchase_seen
"""

import asyncio

from sqlalchemy import inspect, text

from database.db import engine


async def migrate() -> None:
    async with engine.begin() as conn:
        has_events = await conn.run_sync(lambda sync_conn: inspect(sync_conn).has_table("events"))
        if has_events:
            existing = await conn.run_sync(lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("events")})
            if "time" not in existing:
                await conn.execute(text("ALTER TABLE events ADD COLUMN time TIME"))
                print("Добавлена колонка events.time")
            else:
                print("Колонка events.time уже на месте")
        else:
            print("Таблица events ещё не создана — миграция не нужна.")

        has_purchases = await conn.run_sync(lambda sync_conn: inspect(sync_conn).has_table("shop_purchases"))
        if has_purchases:
            existing = await conn.run_sync(
                lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("shop_purchases")}
            )
            if "seen_by_leader" not in existing:
                await conn.execute(text("ALTER TABLE shop_purchases ADD COLUMN seen_by_leader BOOLEAN DEFAULT false"))
                print("Добавлена колонка shop_purchases.seen_by_leader")
            else:
                print("Колонка shop_purchases.seen_by_leader уже на месте")
        else:
            print("Таблица shop_purchases ещё не создана — миграция не нужна.")


if __name__ == "__main__":
    asyncio.run(migrate())
