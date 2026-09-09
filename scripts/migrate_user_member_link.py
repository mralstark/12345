"""Миграция: users.member_id — связь аккаунта с записью в «Составе».

    python -m scripts.migrate_user_member_link
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
        if "member_id" in existing:
            print("Колонка users.member_id уже на месте — миграция не нужна.")
            return

        await conn.execute(text("ALTER TABLE users ADD COLUMN member_id INTEGER"))
        # Уникальность — отдельным индексом: некоторые диалекты (SQLite) не дают
        # добавить UNIQUE через ALTER TABLE ADD COLUMN напрямую.
        await conn.execute(text("CREATE UNIQUE INDEX uq_users_member_id ON users (member_id)"))
        print("Добавлена колонка users.member_id (INTEGER, уникальный индекс)")


if __name__ == "__main__":
    asyncio.run(migrate())
