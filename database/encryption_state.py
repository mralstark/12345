"""Reject an unmigrated database or a mismatched key before serving traffic."""
import hashlib
import json

from sqlalchemy import Text, column, inspect, literal, or_, select, table as sql_table, text

from database.protected_fields import DATE_FIELDS, FIELDS, LOOKUPS
from utils.data_crypto import DataEncryptionError, decrypt_bytes, encrypt_bytes, keyring


def schema_hash() -> str:
    value = {"fields": FIELDS, "lookups": LOOKUPS, "dates": sorted(DATE_FIELDS)}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


async def write_state(conn) -> None:
    active, _ = keyring()
    await conn.execute(text("DELETE FROM data_encryption_state"))
    await conn.execute(text("INSERT INTO data_encryption_state (id,key_id,schema_hash,canary) VALUES (1,:key,:schema,:canary)"), {
        "key": active, "schema": schema_hash(),
        "canary": encrypt_bytes(b"bratstvo-database-ready-v1", "database-canary"),
    })


async def ensure_encryption_ready(conn) -> None:
    active, _ = keyring()
    for table, names in FIELDS.items():
        columns = await conn.run_sync(lambda sync, table=table: inspect(sync).get_columns(table))
        by_name = {c["name"]: c for c in columns}
        if any(name not in by_name or not isinstance(by_name[name]["type"], Text) for name in names.split()):
            raise DataEncryptionError("Database encryption migration is required")
    row = (await conn.execute(text("SELECT key_id,schema_hash,canary FROM data_encryption_state WHERE id=1"))).first()
    if row is None:
        # Only a genuinely empty new database can initialize itself.
        for table, names in FIELDS.items():
            raw = sql_table(table, *(column(name, Text()) for name in names.split()))
            statement = select(literal(1)).select_from(raw).where(or_(*(c.is_not(None) for c in raw.c))).limit(1)
            if (await conn.execute(statement)).first():
                raise DataEncryptionError("Database encryption migration is required")
        await conn.execute(text("INSERT INTO data_encryption_state (id,key_id,schema_hash,canary) VALUES (1,:key,:schema,:canary) ON CONFLICT (id) DO NOTHING"), {
            "key": active, "schema": schema_hash(),
            "canary": encrypt_bytes(b"bratstvo-database-ready-v1", "database-canary"),
        })
        row = (await conn.execute(text("SELECT key_id,schema_hash,canary FROM data_encryption_state WHERE id=1"))).one()
    if row.key_id != active or row.schema_hash != schema_hash():
        raise DataEncryptionError("Database encryption key or schema does not match; migrate offline")
    if decrypt_bytes(row.canary, "database-canary") != b"bratstvo-database-ready-v1":
        raise DataEncryptionError("Database encryption key verification failed")
