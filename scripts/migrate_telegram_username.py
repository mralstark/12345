"""Миграция: telegram_username ("@username") у members и membership_applications.

    python -m scripts.migrate_telegram_username
"""

import asyncio

from sqlalchemy import inspect, text

from database.db import engine


async def _add_column(conn, table: str) -> None:
    has_table = await conn.run_sync(lambda sync_conn: inspect(sync_conn).has_table(table))
    if not has_table:
        print(f"Таблица {table} ещё не создана — миграция не нужна.")
        return

    existing = await conn.run_sync(lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns(table)})
    if "telegram_username" in existing:
        print(f"Колонка {table}.telegram_username уже на месте — миграция не нужна.")
        return

    await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN telegram_username VARCHAR(33)"))
    print(f"Добавлена колонка {table}.telegram_username (VARCHAR(33))")


async def migrate() -> None:
    async with engine.begin() as conn:
        await _add_column(conn, "members")
        await _add_column(conn, "membership_applications")


if __name__ == "__main__":
    asyncio.run(migrate())
