"""Миграция: members.graduated_university, membership_applications.graduated_university —
курс теперь выбирается из списка 1-6 либо «Окончил» (webapp/app.js), при
«Окончил» место работы обязательно (api/routers/members.py, api/routers/register.py).

    python -m scripts.migrate_graduated_university
"""

import asyncio

from sqlalchemy import inspect, text

from database.db import engine

TABLES = ("members", "membership_applications")


async def migrate() -> None:
    async with engine.begin() as conn:
        for table in TABLES:
            has_table = await conn.run_sync(lambda sync_conn, t=table: inspect(sync_conn).has_table(t))
            if not has_table:
                print(f"Таблица {table} ещё не создана — миграция не нужна.")
                continue

            existing = await conn.run_sync(
                lambda sync_conn, t=table: {c["name"] for c in inspect(sync_conn).get_columns(t)}
            )
            if "graduated_university" not in existing:
                await conn.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN graduated_university BOOLEAN NOT NULL DEFAULT FALSE")
                )
                print(f"Добавлена колонка {table}.graduated_university (BOOLEAN, по умолчанию FALSE)")
            else:
                print(f"Колонка {table}.graduated_university уже есть")


if __name__ == "__main__":
    asyncio.run(migrate())
