"""Миграция: убрать users.invite_code — личные INVITE-ссылки упразднены,
единственный источник telegram_id теперь саморегистрация (APPLY_-ссылки).

    python -m scripts.migrate_remove_invite_code
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
        if "invite_code" not in existing:
            print("Колонка users.invite_code уже убрана — миграция не нужна.")
            return

        # SQLite не убирает индекс по колонке сам при DROP COLUMN (в отличие
        # от Postgres) — без этого шага ALTER TABLE падает на ix_users_invite_code.
        await conn.execute(text("DROP INDEX IF EXISTS ix_users_invite_code"))
        await conn.execute(text("ALTER TABLE users DROP COLUMN invite_code"))
        print("Колонка users.invite_code удалена")


if __name__ == "__main__":
    asyncio.run(migrate())
