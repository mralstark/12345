"""Миграция: замена частей-аватара (avatar_hair/mustache/beard/shirt_color)
на выбор готового образа (members.avatar_outfit) — см. api/routers/character.py.

    python -m scripts.migrate_avatar_outfit
"""

import asyncio

from sqlalchemy import inspect, text

from database.db import engine

OLD_COLUMNS = ("avatar_hair", "avatar_mustache", "avatar_beard", "avatar_shirt_color")


async def migrate() -> None:
    async with engine.begin() as conn:
        has_members = await conn.run_sync(lambda sync_conn: inspect(sync_conn).has_table("members"))
        if not has_members:
            print("Таблица members ещё не создана — колонка появится вместе с ней.")
            return

        existing = await conn.run_sync(lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("members")})

        if "avatar_outfit" not in existing:
            await conn.execute(text("ALTER TABLE members ADD COLUMN avatar_outfit VARCHAR(32)"))
            print("Добавлена колонка members.avatar_outfit")
        else:
            print("Колонка members.avatar_outfit уже есть")

        for name in OLD_COLUMNS:
            if name not in existing:
                continue
            try:
                await conn.execute(text(f"ALTER TABLE members DROP COLUMN {name}"))
                print(f"Удалена колонка members.{name}")
            except Exception as exc:  # noqa: BLE001 — старый SQLite без DROP COLUMN
                print(f"Не удалось удалить members.{name} ({exc}) — оставлена как есть, ORM её больше не использует")


if __name__ == "__main__":
    asyncio.run(migrate())
