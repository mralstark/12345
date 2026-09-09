"""Миграция: убрать events.category (тег мероприятия) — признан лишним усложнением.

    python -m scripts.migrate_remove_event_category
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
        if "category" not in existing:
            print("Колонка events.category уже отсутствует — миграция не нужна.")
            return

        await conn.execute(text("ALTER TABLE events DROP COLUMN category"))
        print("Колонка events.category удалена")


if __name__ == "__main__":
    asyncio.run(migrate())
