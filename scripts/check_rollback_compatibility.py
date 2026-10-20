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


def archive_supports_web_identities(path):
    with tarfile.open(path, 'r:gz') as archive:
        names = {name.removeprefix('./'):name for name in archive.getnames()}
        name = names.get('api/auth.py')
        if name is None or 'services/vk_login.py' not in names:
            return False
        source = archive.extractfile(name)
        return source is not None and b'resolve_user_by_id' in source.read()


async def safe_to_restore(path):
    filters_drafts = archive_filters_drafts(path)
    supports_web = archive_supports_web_identities(path)
    async with engine.connect() as conn:
        tables = await conn.run_sync(lambda c:set(inspect(c).get_table_names()))
        if not supports_web and {'external_identities','users'} <= tables:
            web_users = await conn.scalar(text("SELECT count(*) FROM external_identities e JOIN users u ON u.id=e.user_id WHERE u.telegram_id IS NULL"))
            if web_users:
                return False
        if filters_drafts:
            return True
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
        print("Rollback refused: previous code cannot safely handle private drafts or web identities")
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
