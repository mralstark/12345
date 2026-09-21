"""Добавляет управляемый архив в региональный каталог вузов.

Запуск перед обновлённым API:

    python -m scripts.migrate_university_active
"""

import asyncio

from sqlalchemy import inspect, text

from database.db import engine, init_db


async def migrate() -> None:
    await init_db()
    async with engine.begin() as conn:
        columns = await conn.run_sync(
            lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("universities")}
        )
        if "is_active" not in columns:
            boolean_type = "BOOLEAN" if conn.dialect.name == "postgresql" else "INTEGER"
            await conn.execute(
                text(
                    f"ALTER TABLE universities ADD COLUMN is_active {boolean_type} "
                    "NOT NULL DEFAULT TRUE"
                )
            )
            print("Добавлена колонка universities.is_active")
        else:
            print("Колонка universities.is_active уже существует")


if __name__ == "__main__":
    asyncio.run(migrate())
