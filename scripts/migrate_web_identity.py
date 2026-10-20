"""Add VK identities without changing legacy Telegram accounts or applications."""
import asyncio
import re

from sqlalchemy import column, inspect, insert, select, table as sql_table, text

from database.db import engine, init_db


async def migrate():
    await init_db()
    async with engine.begin() as conn:
        sqlite = conn.dialect.name == "sqlite"
        if sqlite:
            # Make SQLite DDL and data copy one transaction, including rollback.
            await conn.exec_driver_sql("BEGIN IMMEDIATE")
        attempts = await conn.run_sync(lambda c:{x['name'] for x in inspect(c).get_columns('oauth_login_attempts')})
        if 'return_article_id' not in attempts:
            await conn.execute(text('ALTER TABLE oauth_login_attempts ADD COLUMN return_article_id INTEGER'))
        for table in ("browser_sessions", "membership_applications"):
            columns = await conn.run_sync(lambda c: {x['name']: x for x in inspect(c).get_columns(table)})
            if "external_identity_id" not in columns:
                cascade = ' ON DELETE CASCADE' if table == 'browser_sessions' else ''
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN external_identity_id INTEGER REFERENCES external_identities(id){cascade}"))
        columns = await conn.run_sync(lambda c: {x['name']: x for x in inspect(c).get_columns('membership_applications')})
        if not columns['telegram_id']['nullable']:
            if not sqlite:
                await conn.execute(text("ALTER TABLE membership_applications ALTER COLUMN telegram_id DROP NOT NULL"))
            else:
                # Preserve the actual table definition, indexes and triggers.
                # No other table currently references membership applications.
                incoming = await conn.run_sync(lambda c: [table for table in inspect(c).get_table_names()
                    for fk in inspect(c).get_foreign_keys(table) if fk['referred_table'] == 'membership_applications'])
                if incoming:
                    raise RuntimeError("SQLite: cannot rebuild applications with incoming foreign keys")
                definition = await conn.scalar(text("SELECT sql FROM sqlite_master WHERE type='table' AND name='membership_applications'"))
                definition, count = re.subn(r'(?i)(\btelegram_id\s+BIGINT)\s+NOT\s+NULL', r'\1', definition, count=1)
                if count != 1:
                    raise RuntimeError("SQLite: unrecognized legacy telegram_id definition")
                definition, count = re.subn(r'(?i)^CREATE TABLE\s+["`\[]?membership_applications["`\]]?',
                    'CREATE TABLE membership_applications_identity_migration', definition, count=1)
                if count != 1:
                    raise RuntimeError("SQLite: unrecognized application table definition")
                if 'ck_application_identity' not in definition:
                    end = definition.rfind(')')
                    definition = definition[:end] + ', CONSTRAINT ck_application_identity CHECK (telegram_id IS NOT NULL OR external_identity_id IS NOT NULL)' + definition[end:]
                retained = list((await conn.execute(text("SELECT sql FROM sqlite_master WHERE tbl_name='membership_applications' AND type IN ('index','trigger') AND sql IS NOT NULL"))).scalars())
                await conn.execute(text(definition))
                old_table = sql_table('membership_applications', *(column(name) for name in columns))
                new_table = sql_table('membership_applications_identity_migration', *(column(name) for name in columns))
                await conn.execute(insert(new_table).from_select(list(columns), select(*old_table.c)))
                await conn.execute(text("DROP TABLE membership_applications"))
                await conn.execute(text("ALTER TABLE membership_applications_identity_migration RENAME TO membership_applications"))
                for statement in retained:
                    await conn.execute(text(statement))
        if not sqlite:
            await conn.execute(text("""DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='ck_application_identity'
                               AND conrelid='membership_applications'::regclass) THEN
                    ALTER TABLE membership_applications ADD CONSTRAINT ck_application_identity
                    CHECK (telegram_id IS NOT NULL OR external_identity_id IS NOT NULL);
                END IF;
            END $$"""))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_browser_sessions_external_identity_id ON browser_sessions(external_identity_id)"))
        await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_membership_applications_pending_external ON membership_applications(external_identity_id) WHERE state='pending'"))
    print("Web identity schema ready; existing accounts preserved")


if __name__ == '__main__':
    asyncio.run(migrate())
