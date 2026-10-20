import hashlib
import io
import tarfile

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine


async def test_legacy_sqlite_migration_preserves_data_constraints_and_indexes(tmp_path, monkeypatch):
    import database.db
    from scripts import migrate_web_identity
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path/'legacy.db').as_posix()}")
    monkeypatch.setattr(database.db,'engine',engine)
    monkeypatch.setattr(migrate_web_identity,'engine',engine)
    try:
        async with engine.begin() as conn:
            await conn.execute(text('CREATE TABLE users (id INTEGER PRIMARY KEY)'))
            await conn.execute(text('CREATE TABLE browser_sessions (secret_hash VARCHAR(64) PRIMARY KEY, user_id INTEGER, grant_hash VARCHAR(64), code VARCHAR(6), expires_at BIGINT)'))
            await conn.execute(text("CREATE TABLE membership_applications (id INTEGER PRIMARY KEY, telegram_id BIGINT NOT NULL, state VARCHAR(16), phone VARCHAR(32))"))
            await conn.execute(text("CREATE UNIQUE INDEX uq_membership_applications_pending_telegram ON membership_applications(telegram_id) WHERE state='pending'"))
            await conn.execute(text("INSERT INTO membership_applications VALUES (4,123456,'pending','+79990000000')"))
            await conn.execute(text("INSERT INTO browser_sessions VALUES ('old-cookie',NULL,'old-grant','123456',9999999999)"))
        await migrate_web_identity.migrate()
        await migrate_web_identity.migrate()
        async with engine.begin() as conn:
            values = (await conn.execute(text('SELECT id,telegram_id,state,phone,external_identity_id FROM membership_applications'))).one()
            assert tuple(values) == (4,123456,'pending','+79990000000',None)
            assert (await conn.scalar(text("SELECT code FROM browser_sessions WHERE secret_hash='old-cookie'"))) == '123456'
            columns = await conn.run_sync(lambda c:{v['name']:v for v in inspect(c).get_columns('membership_applications')})
            assert columns['telegram_id']['nullable']
            indexes = await conn.run_sync(lambda c:{v['name'] for v in inspect(c).get_indexes('membership_applications')})
            assert {'uq_membership_applications_pending_telegram','uq_membership_applications_pending_external'} <= indexes
            await conn.execute(text("INSERT INTO external_identities (id,provider,subject,full_name) VALUES (10,'vk','900001','New VK user')"))
            await conn.execute(text("INSERT INTO membership_applications VALUES (5,NULL,'pending','+79991111111',10)"))
            checks = await conn.run_sync(lambda c:{v['name'] for v in inspect(c).get_check_constraints('membership_applications')})
            assert 'ck_application_identity' in checks
    finally:
        await engine.dispose()


async def test_rollback_without_web_auth_is_refused_for_vk_only_members(tmp_path, monkeypatch):
    from scripts import check_rollback_compatibility as check
    archive_path = tmp_path/'previous.tar.gz'
    with tarfile.open(archive_path,'w:gz') as archive:
        for name in ('services/news.py','services/navigation.py','api/routers/profile.py'):
            source = b'# publication_state filter exists'
            item = tarfile.TarInfo(name); item.size=len(source)
            archive.addfile(item,io.BytesIO(source))
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path/'rollback.db').as_posix()}")
    monkeypatch.setattr(check,'engine',engine)
    try:
        async with engine.begin() as conn:
            await conn.execute(text('CREATE TABLE users (id INTEGER PRIMARY KEY, telegram_id BIGINT)'))
            await conn.execute(text('CREATE TABLE external_identities (id INTEGER PRIMARY KEY, user_id INTEGER)'))
            await conn.execute(text('CREATE TABLE news_posts (id INTEGER PRIMARY KEY, publication_state TEXT)'))
        assert await check.safe_to_restore(archive_path)
        async with engine.begin() as conn:
            await conn.execute(text('INSERT INTO users VALUES (1,NULL)'))
            await conn.execute(text('INSERT INTO external_identities VALUES (1,1)'))
        assert not await check.safe_to_restore(archive_path)
    finally:
        await engine.dispose()
