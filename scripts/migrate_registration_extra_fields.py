"""Миграция: уровень обучения и самостоятельный выбор статуса в анкете
регистрации (запрос «поприветствовать при создании кабинета»).

    python -m scripts.migrate_registration_extra_fields
"""

import asyncio

from sqlalchemy import inspect, text

from database.db import engine

COLUMNS = [
    ("membership_applications", "education_level", "VARCHAR(32)"),
    ("membership_applications", "member_status", "VARCHAR(16)"),
    ("members", "education_level", "VARCHAR(32)"),
]


async def migrate() -> None:
    async with engine.begin() as conn:
        for table, column, sql_type in COLUMNS:
            has_table = await conn.run_sync(lambda sync_conn, t=table: inspect(sync_conn).has_table(t))
            if not has_table:
                print(f"Таблица {table} ещё не создана — колонка появится вместе с ней.")
                continue

            existing_cols = await conn.run_sync(
                lambda sync_conn, t=table: {c["name"] for c in inspect(sync_conn).get_columns(t)}
            )
            if column in existing_cols:
                print(f"Колонка {table}.{column} уже есть")
                continue

            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))
            print(f"Добавлена колонка {table}.{column} ({sql_type}, nullable)")

        # member_status по умолчанию «активист» — уже существующие анкеты
        # (если такие есть) без этого поля тоже должны получить значение.
        has_apps = await conn.run_sync(lambda sync_conn: inspect(sync_conn).has_table("membership_applications"))
        if has_apps:
            await conn.execute(
                text("UPDATE membership_applications SET member_status = 'activist' WHERE member_status IS NULL")
            )


if __name__ == "__main__":
    asyncio.run(migrate())
