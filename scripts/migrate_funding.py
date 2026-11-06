"""Add an empty monthly ledger; authenticate old data before advancing inventory."""
import asyncio
from sqlalchemy import inspect, text
from database.db import engine
from database.models import FundingPlan, FundingEntry, FundingPlanAudit
from database.encryption_state import ensure_encryption_ready, legacy_empty_tables, schema_hash
from utils.data_crypto import DataEncryptionError, decrypt_bytes


async def migrate():
    async with engine.begin() as conn:
        names = await conn.run_sync(lambda c: inspect(c).get_table_names())
        if 'data_encryption_state' in names:
            state = (await conn.execute(text('SELECT schema_hash, canary FROM data_encryption_state WHERE id=1'))).first()
            if state and (state.schema_hash not in {schema_hash(), *legacy_empty_tables()} or
                          decrypt_bytes(state.canary, 'database-canary') != b'bratstvo-database-ready-v1'):
                raise DataEncryptionError('Unsupported encryption inventory')
        for table in (FundingPlan.__table__, FundingEntry.__table__, FundingPlanAudit.__table__):
            await conn.run_sync(lambda c, table=table: table.create(c, checkfirst=True))
        await ensure_encryption_ready(conn)


if __name__ == '__main__':
    asyncio.run(migrate())
