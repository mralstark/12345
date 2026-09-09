"""Миграция: membership_applications.course (план «Снизу вверх», §2) — наша
собственная форма регистрации в Mini App спрашивает курс, в отличие от
внешней формы отбора на academists.ru (см. api/routers/register.py).

    python -m scripts.migrate_application_course
"""

import asyncio

from sqlalchemy import inspect, text

from database.db import engine


async def migrate() -> None:
    async with engine.begin() as conn:
        has_table = await conn.run_sync(lambda sync_conn: inspect(sync_conn).has_table("membership_applications"))
        if not has_table:
            print("Таблица membership_applications ещё не создана — колонка появится вместе с ней.")
            return

        existing_cols = await conn.run_sync(
            lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("membership_applications")}
        )
        if "course" not in existing_cols:
            await conn.execute(text("ALTER TABLE membership_applications ADD COLUMN course INTEGER"))
            print("Добавлена колонка membership_applications.course (INTEGER, nullable)")
        else:
            print("Колонка membership_applications.course уже есть")


if __name__ == "__main__":
    asyncio.run(migrate())
