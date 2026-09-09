"""Миграция: общая лента вместо видимости по уровням.

Новость теперь видят все, кто открыл кабинет, а от чьего имени она — говорит
подпись (NewsPost.byline), сохранённая в момент публикации. Поэтому уходят
scope, cell_id и таблица адресатов-регионов.

Подпись существующим постам проставляется по прежней области видимости:
«all» — от Братства, «regions»/«cell» — от отделения или ячейки. Где определить
не удалось, подставляется имя автора.

    python -m scripts.migrate_news_open_feed
"""

import asyncio

from sqlalchemy import inspect, text

from database.db import engine


async def migrate() -> None:
    async with engine.begin() as conn:
        tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
        if "news_posts" not in tables:
            print("Таблицы news_posts нет — сначала migrate_remove_broadcast_add_news.")
            return

        columns = await conn.run_sync(
            lambda c: {col["name"] for col in inspect(c).get_columns("news_posts")}
        )

        if "byline" not in columns:
            await conn.execute(text("ALTER TABLE news_posts ADD COLUMN byline VARCHAR(128) DEFAULT ''"))
            print("Добавлена колонка byline")

        # Подписи существующим постам — по прежней области видимости.
        if "scope" in columns:
            await conn.execute(text("""
                UPDATE news_posts SET byline = 'Братство Академистов'
                WHERE scope = 'all' AND (byline IS NULL OR byline = '')
            """))
            if "news_post_regions" in tables:
                await conn.execute(text("""
                    UPDATE news_posts SET byline = COALESCE((
                        SELECT 'Академисты | ' || r.name
                        FROM news_post_regions npr JOIN regions r ON r.id = npr.region_id
                        WHERE npr.post_id = news_posts.id LIMIT 1
                    ), byline)
                    WHERE scope = 'regions' AND (byline IS NULL OR byline = '')
                """))
            if "cell_id" in columns:
                await conn.execute(text("""
                    UPDATE news_posts SET byline = COALESCE((
                        SELECT 'Академисты | ' || uc.name
                        FROM university_cells uc WHERE uc.id = news_posts.cell_id
                    ), byline)
                    WHERE scope = 'cell' AND (byline IS NULL OR byline = '')
                """))
            print("Подписи проставлены по прежней области видимости")

        # Кто остался без подписи — подписывается именем автора.
        await conn.execute(text("""
            UPDATE news_posts SET byline = COALESCE((
                SELECT u.full_name FROM users u WHERE u.id = news_posts.author_user_id
            ), 'Академисты')
            WHERE byline IS NULL OR byline = ''
        """))

        if "news_post_regions" in tables:
            await conn.execute(text("DROP TABLE news_post_regions"))
            print("Таблица news_post_regions удалена")

        stale = [c for c in ("scope", "cell_id") if c in columns]
        if stale:
            if conn.dialect.name == "sqlite":
                # SQLite не удаляет колонку, на которую ссылается внешний ключ
                # (cell_id -> university_cells), поэтому таблицу пересобираем.
                await conn.execute(text("""
                    CREATE TABLE news_posts_new (
                        id INTEGER NOT NULL PRIMARY KEY,
                        author_user_id INTEGER NOT NULL REFERENCES users (id),
                        text TEXT NOT NULL,
                        byline VARCHAR(128) DEFAULT '',
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
                    )
                """))
                await conn.execute(text("""
                    INSERT INTO news_posts_new (id, author_user_id, text, byline, created_at)
                    SELECT id, author_user_id, text, COALESCE(byline, ''), created_at FROM news_posts
                """))
                await conn.execute(text("DROP TABLE news_posts"))
                await conn.execute(text("ALTER TABLE news_posts_new RENAME TO news_posts"))
                await conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_news_posts_author_user_id ON news_posts (author_user_id)"
                ))
                print("Таблица news_posts пересобрана без scope и cell_id")
            else:
                for column in stale:
                    await conn.execute(text(f"ALTER TABLE news_posts DROP COLUMN {column}"))
                    print(f"Колонка {column} удалена")

        print("Готово.")


if __name__ == "__main__":
    asyncio.run(migrate())
