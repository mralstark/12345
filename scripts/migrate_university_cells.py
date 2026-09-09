"""Миграция: вузовские ячейки.

Создаёт таблицу university_cells (её ещё нет ни у кого — create_all умеет
создавать целиком недостающие таблицы) и добавляет cell_id в существующие
members/events/transactions через ALTER TABLE ADD COLUMN (create_all новые
колонки в уже существующей таблице не добавляет — см. database/db.py).

    python -m scripts.migrate_university_cells
"""

import asyncio

from sqlalchemy import inspect, text

from database.db import engine
from database.models import Base

TABLES_WITH_CELL_ID = ("members", "events", "transactions")


async def migrate() -> None:
    async with engine.begin() as conn:
        has_table = await conn.run_sync(lambda sync_conn: inspect(sync_conn).has_table("university_cells"))
        if not has_table:
            await conn.run_sync(Base.metadata.tables["university_cells"].create)
            print("Создана таблица university_cells")
        else:
            print("Таблица university_cells уже есть — пропускаю создание")

        for table in TABLES_WITH_CELL_ID:
            has_target = await conn.run_sync(lambda sync_conn, t=table: inspect(sync_conn).has_table(t))
            if not has_target:
                print(f"Таблица {table} ещё не создана — cell_id появится в ней сама при create_all")
                continue
            existing = await conn.run_sync(
                lambda sync_conn, t=table: {c["name"] for c in inspect(sync_conn).get_columns(t)}
            )
            if "cell_id" in existing:
                print(f"Колонка {table}.cell_id уже на месте — пропускаю")
                continue
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN cell_id INTEGER"))
            print(f"Добавлена колонка {table}.cell_id (INTEGER)")


if __name__ == "__main__":
    asyncio.run(migrate())
