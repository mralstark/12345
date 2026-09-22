"""Совмещаемые роли, области бюро, проверка задач и независимая ячейка."""

import asyncio

from sqlalchemy import inspect, text

from database.db import engine
from database.models import Base, BureauRegion


async def migrate() -> None:
    async with engine.begin() as conn:
        timestamp_type = "TIMESTAMP" if conn.dialect.name == "postgresql" else "DATETIME"
        tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
        if "bureau_regions" not in tables:
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=[BureauRegion.__table__]))

        async def add(table: str, column: str, ddl: str) -> None:
            columns = await conn.run_sync(lambda c: {x["name"] for x in inspect(c).get_columns(table)})
            if column not in columns:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))

        for column, ddl in (
            ("is_superuser", "BOOLEAN NOT NULL DEFAULT FALSE"),
            ("is_federal", "BOOLEAN NOT NULL DEFAULT FALSE"),
            ("is_coordinator", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ):
            await add("users", column, ddl)
        await conn.execute(text("UPDATE users SET is_superuser = TRUE WHERE role = 'superuser'"))
        await conn.execute(text("UPDATE users SET is_federal = TRUE WHERE role = 'federal'"))
        await conn.execute(text("UPDATE users SET is_coordinator = TRUE WHERE role = 'coordinator'"))

        for column, ddl in (
            ("submitted_at", timestamp_type),
            ("reviewed_at", timestamp_type),
            ("review_note", "VARCHAR(500)"),
        ):
            await add("tasks", column, ddl)
        await add("membership_applications", "cell_id", "INTEGER")
        await add("quests", "description", "VARCHAR(500)")
        for column, ddl in (
            ("pending_count", "INTEGER NOT NULL DEFAULT 0"),
            ("submitted_at", timestamp_type),
            ("submitted_note", "VARCHAR(500)"),
        ):
            await add("member_quest_progress", column, ddl)

    print("Операционные роли и проверка выполнения готовы")


if __name__ == "__main__":
    asyncio.run(migrate())
