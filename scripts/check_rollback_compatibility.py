"""Refuse old readers incompatible with drafts, identities or encrypted storage."""
import ast
import asyncio
import hashlib
import json
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
        if "data_encryption_state" in tables and (await conn.execute(text("SELECT 1 FROM data_encryption_state LIMIT 1"))).first():
            with tarfile.open(path, "r:gz") as archive:
                names = {name.removeprefix("./") for name in archive.getnames()}
                if not {"database/encrypted.py", "database/protected_fields.py", "utils/data_crypto.py", "utils/private_files.py"} <= names:
                    return False
            # Having encryption helpers is insufficient: an older reader may
            # reject the new authenticated inventory after an additive release.
            state = (await conn.execute(text("SELECT schema_hash FROM data_encryption_state WHERE id=1"))).scalar_one_or_none()
            if state != archive_encryption_schema(path):
                return False
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


def archive_encryption_schema(path):
    """Read literal inventory declarations without executing archived code."""
    try:
        with tarfile.open(path, "r:gz") as archive:
            name = next(x for x in archive.getnames() if x.removeprefix('./') == 'database/protected_fields.py')
            source = archive.extractfile(name)
            tree = ast.parse(source.read().decode('utf-8-sig'))
        values = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
                  if isinstance(node, ast.Assign) and len(node.targets) == 1
                  and isinstance(node.targets[0], ast.Name)
                  and node.targets[0].id in {'FIELDS', 'LOOKUPS', 'DATE_FIELDS'}}
        payload = {'fields': values['FIELDS'], 'lookups': values['LOOKUPS'], 'dates': sorted(values['DATE_FIELDS'])}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    except (ValueError, KeyError, AttributeError, SyntaxError, StopIteration, UnicodeError):
        return None


async def main():
    try:
        safe = await safe_to_restore(sys.argv[1])
    except Exception:
        safe = False
    finally:
        await engine.dispose()
    if not safe:
        print("Rollback refused: previous code is incompatible with drafts, identities or the encrypted field inventory")
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
