"""Миграция: поле «О себе» в карточке члена состава.

    python -m scripts.migrate_member_about
"""

import asyncio

from sqlalchemy import inspect, text

from database.db import engine


async def migrate() -> None:
    async with engine.begin() as conn:
        columns = await conn.run_sync(
            lambda c: {col["name"] for col in inspect(c).get_columns("members")}
        )
        if "about" in columns:
            print("Колонка about уже есть — миграция не нужна.")
            return
        await conn.execute(text("ALTER TABLE members ADD COLUMN about VARCHAR(600)"))
        print("Добавлена колонка members.about")


if __name__ == "__main__":
    asyncio.run(migrate())
