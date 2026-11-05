"""Bounded, cached database/key readiness; liveness alone cannot detect DB loss."""
import asyncio
import time

from sqlalchemy import text
from database.db import engine
from database.encryption_state import schema_hash
from utils.data_crypto import decrypt_bytes, keyring

_lock = asyncio.Lock()
_checked_at = float('-inf')
_ready = False


async def _probe():
    async with engine.connect() as connection:
        row = (await connection.execute(text('SELECT key_id,schema_hash,canary FROM data_encryption_state WHERE id=1'))).first()
        active, _ = keyring()
        return bool(row and row.key_id == active and row.schema_hash == schema_hash()
                    and decrypt_bytes(row.canary, 'database-canary') == b'bratstvo-database-ready-v1')


async def ready():
    global _checked_at, _ready
    if time.monotonic() - _checked_at < (2 if _ready else 1):
        return _ready
    async with _lock:
        if time.monotonic() - _checked_at >= (2 if _ready else 1):
            try:
                _ready = await asyncio.wait_for(_probe(), timeout=3)
            except Exception:
                _ready = False
            _checked_at = time.monotonic()
    return _ready
