"""Миграция: members.stars (валюта геймификации) + переименование задания
«Посетить лекцию» -> «Посетить мероприятие» (см. api/routers/character.py::
BRANCHES — теперь это входная ступень ветки «Организатор»).

    python -m scripts.migrate_member_stars
"""

import asyncio

from sqlalchemy import inspect, text

from database.db import engine

OLD_QUEST_TITLE = "Посетить лекцию"
NEW_QUEST_TITLE = "Посетить мероприятие"


async def migrate() -> None:
    async with engine.begin() as conn:
        has_members = await conn.run_sync(lambda sync_conn: inspect(sync_conn).has_table("members"))
        if has_members:
            existing = await conn.run_sync(lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("members")})
            if "stars" not in existing:
                await conn.execute(text("ALTER TABLE members ADD COLUMN stars INTEGER DEFAULT 0"))
                print("Добавлена колонка members.stars")
            else:
                print("Колонка members.stars уже есть")
        else:
            print("Таблица members ещё не создана — колонка появится вместе с ней.")

        has_quests = await conn.run_sync(lambda sync_conn: inspect(sync_conn).has_table("quests"))
        if has_quests:
            result = await conn.execute(
                text("UPDATE quests SET title = :new WHERE title = :old"),
                {"new": NEW_QUEST_TITLE, "old": OLD_QUEST_TITLE},
            )
            if result.rowcount:
                print(f"Задание переименовано: «{OLD_QUEST_TITLE}» -> «{NEW_QUEST_TITLE}»")
            else:
                print("Переименовывать нечего (задания «Посетить лекцию» нет или уже переименовано)")


if __name__ == "__main__":
    asyncio.run(migrate())
