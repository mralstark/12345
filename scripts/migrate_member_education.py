"""Миграция: вуз/факультет/курс/место работы на существующей таблице members.

init_db()/create_all умеет только создавать недостающие таблицы целиком —
новые колонки в уже существующей таблице им не добавить (см. комментарий
в database/db.py). Этот скрипт — разовая ручная миграция для БД, где
'members' уже существует (локальная разработка, сервер после апдейта).
На новой, ещё не созданной БД ничего не делает — create_all и так создаст
таблицу сразу с этими колонками.

    python -m scripts.migrate_member_education
"""

import asyncio

from sqlalchemy import inspect, text

from database.db import engine

NEW_COLUMNS = {
    "university": "VARCHAR(255)",
    "faculty": "VARCHAR(255)",
    "course": "INTEGER",
    "study_years": "INTEGER",
    "workplace": "VARCHAR(255)",
    "course_bumped_year": "INTEGER",
    "joined_at": "DATE",
}


async def migrate() -> None:
    async with engine.begin() as conn:
        has_table = await conn.run_sync(lambda sync_conn: inspect(sync_conn).has_table("members"))
        if not has_table:
            # Таблицы ещё нет — её создаст create_all (init_db) сразу с новыми колонками.
            print("Таблица members ещё не создана — миграция не нужна.")
            return

        existing = await conn.run_sync(lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("members")})

        missing = {name: ddl for name, ddl in NEW_COLUMNS.items() if name not in existing}
        if not missing:
            print("Все колонки уже на месте — миграция не нужна.")
            return

        for name, ddl in missing.items():
            await conn.execute(text(f"ALTER TABLE members ADD COLUMN {name} {ddl}"))
            print(f"Добавлена колонка members.{name} ({ddl})")

        if "study_years" in missing:
            # У существующих записей должно быть значение по умолчанию модели (4),
            # а не NULL — ALTER TABLE ADD COLUMN не подставляет server_default задним числом.
            await conn.execute(text("UPDATE members SET study_years = 4 WHERE study_years IS NULL"))
            print("study_years у существующих записей проставлен по умолчанию — 4")


if __name__ == "__main__":
    asyncio.run(migrate())
