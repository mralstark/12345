"""Миграция: свой круглый аватар и вид подписи поста.

    python -m scripts.migrate_avatars_and_byline_kind
"""

import asyncio

from sqlalchemy import inspect, text

from database.db import engine


async def migrate() -> None:
    async with engine.begin() as conn:
        members = await conn.run_sync(
            lambda c: {col["name"] for col in inspect(c).get_columns("members")}
        )
        if "avatar_path" not in members:
            await conn.execute(text("ALTER TABLE members ADD COLUMN avatar_path VARCHAR(512)"))
            print("Добавлена колонка members.avatar_path")

        posts = await conn.run_sync(
            lambda c: {col["name"] for col in inspect(c).get_columns("news_posts")}
        )
        if "byline_kind" not in posts:
            await conn.execute(text(
                "ALTER TABLE news_posts ADD COLUMN byline_kind VARCHAR(16) NOT NULL DEFAULT 'personal'"
            ))
            print("Добавлена колонка news_posts.byline_kind")
            # Существующие посты: подпись «Братство Академистов» — от Братства,
            # «Академисты | …» — от отделения, остальное подписано человеком.
            await conn.execute(text(
                "UPDATE news_posts SET byline_kind = 'bratstvo' WHERE byline = 'Братство Академистов'"
            ))
            await conn.execute(text(
                "UPDATE news_posts SET byline_kind = 'otdelenie' WHERE byline LIKE 'Академисты%'"
            ))
            print("Вид подписи проставлен существующим постам")

        print("Готово.")


if __name__ == "__main__":
    asyncio.run(migrate())
