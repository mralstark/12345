"""Repeatable additive schema update, compatible with PostgreSQL and SQLite."""
import asyncio

from sqlalchemy import inspect, text

from database.db import engine


async def migrate():
    async with engine.begin() as conn:
        for table, changes in {
            "news_posts": {
                "publication_state": "VARCHAR(16) NOT NULL DEFAULT 'published'",
                "content_kind": "VARCHAR(16) NOT NULL DEFAULT 'post'",
                "article_title": "VARCHAR(256)", "article_summary": "VARCHAR(500)",
                "article_content": "TEXT", "article_revision": "INTEGER NOT NULL DEFAULT 1",
                "article_source_id": "INTEGER", "article_source_revision": "INTEGER",
                "article_updated_at": "TIMESTAMP",
            },
            "member_quest_progress": {"personal_seen_token": "VARCHAR(64)"},
        }.items():
            columns = await conn.run_sync(lambda c, t=table: {x["name"] for x in inspect(c).get_columns(t)})
            for column, ddl in changes.items():
                if column not in columns:
                    await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_news_posts_publication_state ON news_posts(publication_state)"))
    print("Articles and reward notifications schema ready")


if __name__ == "__main__":
    asyncio.run(migrate())
