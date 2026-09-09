"""Миграция: events.reminder_sent_on — маркер отправки напоминания за день.

    python -m scripts.migrate_event_reminder
"""

import asyncio

from sqlalchemy import inspect, text

from database.db import engine


async def migrate() -> None:
    async with engine.begin() as conn:
        has_table = await conn.run_sync(lambda sync_conn: inspect(sync_conn).has_table("events"))
        if not has_table:
            print("Таблица events ещё не создана — миграция не нужна.")
            return

        existing = await conn.run_sync(lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("events")})
        if "reminder_sent_on" in existing:
            print("Колонка events.reminder_sent_on уже на месте — миграция не нужна.")
            return

        await conn.execute(text("ALTER TABLE events ADD COLUMN reminder_sent_on DATE"))
        print("Добавлена колонка events.reminder_sent_on (DATE)")


if __name__ == "__main__":
    asyncio.run(migrate())
