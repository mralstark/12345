"""Аддитивная, повторяемая миграция октябрьских правок (SQLite / PostgreSQL)."""
import asyncio

from sqlalchemy import inspect, text

from database.db import engine
from database.models import Base, QuestActivity, SectionRead


async def migrate() -> None:
    async with engine.begin() as conn:
        for table, column, ddl in (
            ("news_posts", "region_id", "INTEGER REFERENCES regions(id)"),
            ("member_quest_progress", "earned_stars_floor", "INTEGER NOT NULL DEFAULT 0"),
        ):
            columns = await conn.run_sync(lambda c, t=table: {x["name"] for x in inspect(c).get_columns(t)})
            if column not in columns:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_news_posts_region_id ON news_posts(region_id)"))
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=[QuestActivity.__table__, SectionRead.__table__]))
    print("Схема кабинетов обновлена")


if __name__ == "__main__":
    asyncio.run(migrate())
