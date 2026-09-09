"""Миграция: убрать «Почту» (личные сообщения между пользователями) целиком —
признана лишней, вместе с UI и бэкендом (api/routers/mail.py, handlers/mail.py).
Рассылка, о которой тут говорилось как об остающейся, позже тоже удалена —
см. scripts/migrate_remove_broadcast_add_news.py.

    python -m scripts.migrate_remove_mail
"""

import asyncio

from sqlalchemy import inspect, text

from database.db import engine


async def migrate() -> None:
    async with engine.begin() as conn:
        has_table = await conn.run_sync(lambda sync_conn: inspect(sync_conn).has_table("messages"))
        if not has_table:
            print("Таблица messages уже отсутствует — миграция не нужна.")
            return

        await conn.execute(text("DROP TABLE messages"))
        print("Таблица messages удалена")


if __name__ == "__main__":
    asyncio.run(migrate())
