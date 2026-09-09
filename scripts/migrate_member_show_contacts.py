"""Миграция: флаг «показывать контакты в профиле» у члена состава.

Профиль человека открывается из ленты любому члену Братства, но телефон и
телеграм он сдавал для учёта, а не для показа. Поэтому по умолчанию скрыты,
и человек включает их сам в «Личной информации».

    python -m scripts.migrate_member_show_contacts
"""

import asyncio

from sqlalchemy import inspect, text

from database.db import engine


async def migrate() -> None:
    async with engine.begin() as conn:
        columns = await conn.run_sync(
            lambda c: {col["name"] for col in inspect(c).get_columns("members")}
        )
        if "show_contacts" in columns:
            print("Колонка show_contacts уже есть — миграция не нужна.")
            return
        await conn.execute(
            text("ALTER TABLE members ADD COLUMN show_contacts BOOLEAN NOT NULL DEFAULT FALSE")
        )
        print("Добавлена колонка members.show_contacts (по умолчанию скрыто)")


if __name__ == "__main__":
    asyncio.run(migrate())
