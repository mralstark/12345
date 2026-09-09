"""Миграция: member_quest_progress.stars_claimed — звёзды теперь не
начисляются автоматически при «+1» руководителя, а забираются самим
человеком по кнопке «Получить» (см. api/routers/character.py::claim_quest_stars).

    python -m scripts.migrate_quest_claims
"""

import asyncio

from sqlalchemy import inspect, text

from database.db import engine


async def migrate() -> None:
    async with engine.begin() as conn:
        has_table = await conn.run_sync(lambda sync_conn: inspect(sync_conn).has_table("member_quest_progress"))
        if not has_table:
            print("Таблица member_quest_progress ещё не создана — колонка появится вместе с ней.")
            return

        existing = await conn.run_sync(
            lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("member_quest_progress")}
        )
        if "stars_claimed" not in existing:
            await conn.execute(text("ALTER TABLE member_quest_progress ADD COLUMN stars_claimed INTEGER DEFAULT 0"))
            print("Добавлена колонка member_quest_progress.stars_claimed")
        else:
            print("Колонка member_quest_progress.stars_claimed уже есть")


if __name__ == "__main__":
    asyncio.run(migrate())
