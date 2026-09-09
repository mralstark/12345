"""Миграция: regions.genitive_name — для подписи «Руководитель Академистов {...}».

    python -m scripts.migrate_region_genitive
"""

import asyncio

from sqlalchemy import inspect, text

from database.db import engine


async def migrate() -> None:
    async with engine.begin() as conn:
        has_table = await conn.run_sync(lambda sync_conn: inspect(sync_conn).has_table("regions"))
        if not has_table:
            print("Таблица regions ещё не создана — миграция не нужна.")
            return

        existing = await conn.run_sync(lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("regions")})
        if "genitive_name" in existing:
            print("Колонка regions.genitive_name уже на месте — миграция не нужна.")
            return

        await conn.execute(text("ALTER TABLE regions ADD COLUMN genitive_name VARCHAR(128)"))
        print("Добавлена колонка regions.genitive_name (VARCHAR(128))")


if __name__ == "__main__":
    asyncio.run(migrate())
