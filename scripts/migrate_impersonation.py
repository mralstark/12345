"""Миграция: users.view_as_user_id — режим «войти как» для superuser.

    python -m scripts.migrate_impersonation
"""

import asyncio

from sqlalchemy import inspect, text

from database.db import engine


async def migrate() -> None:
    async with engine.begin() as conn:
        has_table = await conn.run_sync(lambda sync_conn: inspect(sync_conn).has_table("users"))
        if not has_table:
            print("Таблица users ещё не создана — миграция не нужна.")
            return

        existing = await conn.run_sync(lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("users")})
        if "view_as_user_id" in existing:
            print("Колонка users.view_as_user_id уже на месте — миграция не нужна.")
            return

        await conn.execute(text("ALTER TABLE users ADD COLUMN view_as_user_id INTEGER"))
        print("Добавлена колонка users.view_as_user_id (INTEGER)")


if __name__ == "__main__":
    asyncio.run(migrate())
