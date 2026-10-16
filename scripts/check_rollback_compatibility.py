"""Refuse old readers that would expose private article drafts during rollback."""
import asyncio
import sys
import tarfile

from sqlalchemy import inspect, text

from database.db import engine


def archive_filters_drafts(path):
    with tarfile.open(path, "r:gz") as archive:
        names = {name.removeprefix("./"): name for name in archive.getnames()}
        for name in ("services/news.py", "services/navigation.py", "api/routers/profile.py"):
            source = archive.extractfile(names[name])
            if source is None or b"publication_state" not in source.read():
                return False
    return True


async def safe_to_restore(path):
    if archive_filters_drafts(path):
        return True
    async with engine.connect() as conn:
        columns = await conn.run_sync(lambda c: {x["name"] for x in inspect(c).get_columns("news_posts")})
        if "publication_state" not in columns:
            return True
        drafts = (await conn.execute(text("SELECT COUNT(*) FROM news_posts WHERE publication_state = 'draft'"))).scalar()
        return not drafts


async def main():
    try:
        safe = await safe_to_restore(sys.argv[1])
    except Exception:
        safe = False
    finally:
        await engine.dispose()
    if not safe:
        print("Rollback refused: the previous reader may expose private article drafts")
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
