"""Миграция: universities.region_id (план «Снизу вверх», §3) — привязка вуза
к отделению, чтобы форма регистрации показывала только вузы выбранного
города, а не весь каталог сразу.

    python -m scripts.migrate_university_region
"""

import asyncio

from sqlalchemy import inspect, text

from database.db import engine


async def migrate() -> None:
    async with engine.begin() as conn:
        has_table = await conn.run_sync(lambda sync_conn: inspect(sync_conn).has_table("universities"))
        if not has_table:
            print("Таблица universities ещё не создана — колонка появится вместе с ней.")
            return

        existing_cols = await conn.run_sync(lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("universities")})
        if "region_id" not in existing_cols:
            await conn.execute(text("ALTER TABLE universities ADD COLUMN region_id INTEGER"))
            print("Добавлена колонка universities.region_id (INTEGER, nullable)")
        else:
            print("Колонка universities.region_id уже есть")

    print(
        "Готово. Существующие записи каталога останутся без региона, пока не "
        "будут сопоставлены вручную или через scripts/seed_universities.py "
        "(идемпотентно — по имени)."
    )


if __name__ == "__main__":
    asyncio.run(migrate())
