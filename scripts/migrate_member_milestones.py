"""Миграция: три вехи членства вместо одной «даты вступления».

members.joined_at → members.activist_joined_at (переименование, те же
значения — раньше это и была дата вступления в Академисты), плюс новые
members.member_inducted_at («Посвящение в Братство») и
members.alumni_graduated_at («Выпуск из студенческого Братства»).

    python -m scripts.migrate_member_milestones
"""

import asyncio

from sqlalchemy import inspect, text

from database.db import engine


async def migrate() -> None:
    async with engine.begin() as conn:
        has_table = await conn.run_sync(lambda sync_conn: inspect(sync_conn).has_table("members"))
        if not has_table:
            print("Таблица members ещё не создана — миграция не нужна.")
            return

        existing = await conn.run_sync(lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("members")})

        if "joined_at" in existing and "activist_joined_at" not in existing:
            await conn.execute(text("ALTER TABLE members RENAME COLUMN joined_at TO activist_joined_at"))
            print("Переименована колонка members.joined_at в activist_joined_at")
        elif "activist_joined_at" not in existing:
            await conn.execute(text("ALTER TABLE members ADD COLUMN activist_joined_at DATE"))
            print("Добавлена колонка members.activist_joined_at (DATE)")
        else:
            print("Колонка members.activist_joined_at уже есть")

        for name in ("member_inducted_at", "alumni_graduated_at"):
            existing = await conn.run_sync(lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("members")})
            if name not in existing:
                await conn.execute(text(f"ALTER TABLE members ADD COLUMN {name} DATE"))
                print(f"Добавлена колонка members.{name} (DATE)")
            else:
                print(f"Колонка members.{name} уже есть")


if __name__ == "__main__":
    asyncio.run(migrate())
