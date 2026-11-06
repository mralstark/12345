"""Copy into a NEW encrypted DB/storage; never rewrite the only original copy.

URLs are environment-only, never command-line arguments. Both services must be
stopped for the final copy. Source remains untouched for recovery. A fresh target
also avoids placing legacy plaintext into its WAL or obsolete database pages.
"""
import argparse
import asyncio
import base64
import json
import os
import secrets
from pathlib import Path

from sqlalchemy import MetaData, Text, cast, func, literal, select, text, type_coerce
from sqlalchemy.dialects.postgresql import REGCLASS
from sqlalchemy.ext.asyncio import create_async_engine

from database.encrypted import EncryptedValue
from database.encryption_state import ensure_encryption_ready, schema_hash, write_state
from database.models import Base
from utils.data_crypto import FILE_PREFIX, PREFIX, DataEncryptionError, decrypt_bytes, decrypt_file_bytes, encrypt_bytes, keyring


def generate_key(output: Path, key_id: str = "primary") -> None:
    if not output.is_absolute() or output.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
        raise ValueError("Use an absolute private path outside the source tree")
    output = output.resolve()
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    value = {"active": key_id, "keys": {key_id: base64.urlsafe_b64encode(secrets.token_bytes(64)).decode("ascii")}}
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())


def copy_order(tables):
    """Defer only nullable cycle edges; other optional FKs may be CHECK-required."""
    # Funding cancellations must retain their original-entry reference at insert
    # time (a CHECK requires it). Their self-edge is handled by row ordering.
    row_ordered = {("funding_entries", "cancels_id")}
    edges = {name: [(column.name, fk.column.table.name, column.nullable)
                    for column in table.columns for fk in column.foreign_keys
                    if (name, column.name) not in row_ordered]
             for name, table in tables.items()}
    deferred = {(name, field) for name, links in edges.items() for field, target, nullable in links
                if name == target and nullable}
    pending = set(tables)
    ordered = []
    while pending:
        ready = sorted(name for name in pending if all(
            target not in pending or (name, field) in deferred for field, target, _ in edges[name]
        ))
        if not ready:
            visited = set()

            def cycle(node, trail, links):
                visited.add(node)
                for field, target, nullable in edges[node]:
                    if target not in pending or (node, field) in deferred:
                        continue
                    edge = (node, field, target, nullable)
                    if target in trail:
                        return links[trail.index(target):] + [edge]
                    if target not in visited:
                        found = cycle(target, trail + [target], links + [edge])
                        if found:
                            return found
                return None

            found = next((result for name in sorted(pending) if name not in visited
                          if (result := cycle(name, [name], []))), None)
            candidate = next(((node, field) for node, field, _, nullable in (found or []) if nullable), None)
            if candidate is None:
                raise RuntimeError("Non-nullable foreign key cycle requires a dedicated migration")
            deferred.add(candidate)
            continue
        ordered.extend(ready)
        pending.difference_update(ready)
    return ordered, deferred


async def verify_database(conn) -> dict:
    await ensure_encryption_ready(conn)
    counts = {}
    active, _ = keyring()
    for table in Base.metadata.tables.values():
        protected = [column for column in table.columns if isinstance(column.type, EncryptedValue)]
        if not protected:
            continue
        statement = select(*(type_coerce(c, Text()).label(c.name) for c in protected)).select_from(table)
        result = await conn.execute(statement)
        checked = 0
        for row in result.mappings():
            for column in protected:
                value = row[column.name]
                if value is None:
                    continue
                if not value.startswith(f"{PREFIX}{active}:"):
                    raise DataEncryptionError("Legacy data or an old key remains in the encrypted database")
                column.type.process_result_value(value, conn.dialect)
                checked += 1
        counts[table.name] = checked
    return counts


async def migrate_database(source_url: str, target_url: str, source_storage: Path | None = None) -> dict:
    from config import STORAGE_DIR
    from utils.private_files import MAX_PRIVATE_BYTES, private_path, write_private_file

    if source_url == target_url:
        raise ValueError("Migration requires a separate empty target database")
    keyring()
    if source_storage is not None and source_storage.resolve() == STORAGE_DIR.resolve():
        raise ValueError("Migration requires a separate target storage directory")
    source = create_async_engine(source_url, hide_parameters=True)
    target = create_async_engine(target_url, hide_parameters=True)
    counts, staged, files = {}, [], set()
    tables = {name: table for name, table in Base.metadata.tables.items() if name != "data_encryption_state"}
    try:
        async with source.connect() as original, target.begin() as destination:
            if original.dialect.name == "postgresql":
                await original.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
                # No old bot/API may retain a connection during the cutover.
                others = (await original.execute(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND backend_type='client backend'"))).scalar_one()
                if others:
                    raise RuntimeError("Stop all source database clients before migration")
            await destination.run_sync(Base.metadata.create_all)
            for table in tables.values():
                if (await destination.execute(select(literal(1)).select_from(table).limit(1))).first():
                    raise RuntimeError("Target database is not empty")
            metadata = MetaData()
            await original.run_sync(metadata.reflect)
            for name in set(metadata.tables) - set(tables) - {"data_encryption_state"}:
                if (await original.execute(select(metadata.tables[name]).limit(1))).first():
                    raise RuntimeError("An unrecognized source table contains data; review it before copying")
            source_encrypted = False
            if "data_encryption_state" in metadata.tables:
                state = (await original.execute(text("SELECT schema_hash,canary FROM data_encryption_state WHERE id=1"))).first()
                if state:
                    from database.encryption_state import legacy_empty_tables
                    if state.schema_hash not in {schema_hash(), *legacy_empty_tables()} or decrypt_bytes(state.canary, "database-canary") != b"bratstvo-database-ready-v1":
                        raise DataEncryptionError("Source encryption schema or key is unsupported")
                    for name in legacy_empty_tables().get(state.schema_hash, ()):
                        if name in metadata.tables and (await original.execute(select(metadata.tables[name]).limit(1))).first():
                            raise DataEncryptionError("Unmarked protected data requires manual review")
                    source_encrypted = True
            ordered, deferred = copy_order(tables)
            for name in ordered:
                table = tables[name]
                if name not in metadata.tables:
                    counts[name] = 0
                    continue
                rows = (await original.execute(select(metadata.tables[name]))).mappings().all()
                if name == "funding_entries":
                    rows.sort(key=lambda row: (row['cancels_id'] is not None, row['id']))
                counts[name] = len(rows)
                for row in rows:
                    values = {c.name: row[c.name] for c in table.columns if c.name in row}
                    for column in table.columns:
                        value = values.get(column.name)
                        if isinstance(column.type, EncryptedValue) and value is not None:
                            if source_encrypted:
                                values[column.name] = column.type.process_result_value(value, original.dialect)
                        if (name, column.name) in deferred and value is not None:
                            staged.append((name, {pk.name: values[pk.name] for pk in table.primary_key}, column.name, value))
                            values[column.name] = None
                    for field in ("avatar_path", "stored_path"):
                        if values.get(field):
                            files.add(values[field])
                    await destination.execute(table.insert().values(**values))
            for name, identity, column, value in staged:
                statement = tables[name].update()
                for field, primary in identity.items():
                    statement = statement.where(tables[name].c[field] == primary)
                await destination.execute(statement.values({column: value}))
            for name, count in counts.items():
                actual = (await destination.execute(select(func.count()).select_from(tables[name]))).scalar_one()
                if actual != count:
                    raise RuntimeError("Migration row count mismatch")
                if name not in metadata.tables:
                    continue
                table = tables[name]
                new_rows = (await destination.execute(select(table))).mappings().all()
                index = {tuple(row[pk.name] for pk in table.primary_key): row for row in new_rows}
                for row in (await original.execute(select(metadata.tables[name]))).mappings():
                    expected = {c.name: row[c.name] for c in table.columns if c.name in row}
                    for column in table.columns:
                        value = expected.get(column.name)
                        if isinstance(column.type, EncryptedValue) and value is not None and source_encrypted:
                            expected[column.name] = column.type.process_result_value(value, original.dialect)
                    actual_row = index[tuple(expected[pk.name] for pk in table.primary_key)]
                    if any(actual_row[field] != value for field, value in expected.items()):
                        raise DataEncryptionError("Migrated values do not match the source")
            # Explicit integer IDs must not leave PostgreSQL sequences at 1.
            if destination.dialect.name == "postgresql":
                for name, table in tables.items():
                    if "id" in table.c and not isinstance(table.c.id.type, EncryptedValue):
                        sequence = (await destination.execute(text("SELECT pg_get_serial_sequence(:table,'id')"), {"table": name})).scalar_one()
                        if sequence:
                            maximum = select(func.max(table.c.id)).scalar_subquery()
                            populated = select(func.count()).select_from(table).scalar_subquery() > 0
                            await destination.execute(select(func.setval(cast(sequence, REGCLASS()), func.coalesce(maximum, 1), populated)))
            copied_files = 0
            for relative in sorted(files):
                if source_storage is None:
                    raise RuntimeError("SOURCE_STORAGE_DIR is required for existing private files")
                old = (source_storage / relative).resolve()
                new, canonical = private_path(STORAGE_DIR / relative)
                if not old.is_relative_to(source_storage.resolve()) or not old.is_file():
                    raise RuntimeError("A referenced private file is missing or outside source storage")
                if old.stat().st_size > MAX_PRIVATE_BYTES + 128:
                    raise RuntimeError("A referenced file exceeds the private file limit")
                payload = await asyncio.to_thread(old.read_bytes)
                if source_encrypted:
                    payload = decrypt_file_bytes(payload, canonical)
                if new.exists():
                    raise RuntimeError("Target private storage is not empty")
                await asyncio.to_thread(write_private_file, new, payload)
                from utils.private_files import read_private_file
                if await asyncio.to_thread(read_private_file, new) != payload:
                    raise DataEncryptionError("Migrated private file does not match the source")
                copied_files += 1
            await write_state(destination)
            checked = await verify_database(destination)
        return {"rows": counts, "encrypted_values": checked, "encrypted_files": copied_files}
    finally:
        await source.dispose()
        await target.dispose()


async def main(args) -> None:
    if args.command == "generate-key":
        generate_key(Path(args.output))
        print(json.dumps({"key_created": True}))
        return
    if args.command in {"rotate-key", "export-active-key"}:
        active, keys = keyring()
        keys = dict(keys)
        if args.command == "rotate-key":
            if len(keys) >= 16:
                raise RuntimeError("Archive historical key generations before rotating again")
            active = "k" + secrets.token_hex(8)
            keys[active] = secrets.token_bytes(64)
        else:
            keys = {active: keys[active]}
        value = {"active": active, "keys": {name: base64.urlsafe_b64encode(key).decode("ascii") for name, key in keys.items()}}
        path = Path(args.output)
        if not path.is_absolute() or path.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
            raise ValueError("Use an absolute private path outside the source tree")
        path = path.resolve()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        print(json.dumps({"key_created": True, "historical_keys": len(keys) - 1}))
        return
    if args.command in {"seal-bootstrap", "seal-runtime"}:
        from dotenv import load_dotenv
        load_dotenv()
        if args.command == "seal-runtime":
            names = ("BOT_TOKEN", "DATABASE_URL", "MEDIA_SIGNING_KEY", "BOT_PROXY_URL", "VK_MINI_APP_SECRET")
            previous = os.environ.get("DATA_RUNTIME_CIPHERTEXT", "").strip()
            payload = json.loads(decrypt_bytes(previous, "runtime-secrets")) if previous else {}
            for name in names:
                value = os.environ.get(name, "").strip()
                if value or name not in payload:
                    payload[name] = value
            if any(not payload.get(name) for name in names[:3]):
                raise RuntimeError("Missing mandatory runtime settings to seal")
            sealed = encrypt_bytes(json.dumps(payload).encode(), "runtime-secrets")
            fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="ascii") as stream:
                stream.write(sealed)
            print(json.dumps({"runtime_sealed": True}))
            return
        names = ("SUPERUSER_TELEGRAM_IDS", "AUTO_FEDERAL_TELEGRAM_IDS", "PRIMARY_REVIEWER_FULL_NAME", "OPERATIONS_ALERT_TELEGRAM_ID")
        previous = os.environ.get("DATA_BOOTSTRAP_CIPHERTEXT", "").strip()
        if previous:
            if any(os.environ.get(name, "").strip() for name in names):
                raise RuntimeError("Resolve mixed plaintext and encrypted bootstrap settings first")
            payload = json.loads(decrypt_bytes(previous, "runtime-bootstrap"))
        else:
            payload = {name: [int(x) for x in os.environ.get(name, "").replace(" ", "").split(",") if x] for name in names[:2]}
            payload.update({name: os.environ.get(name, "").strip() for name in names[2:]})
        sealed = encrypt_bytes(json.dumps(payload, ensure_ascii=False).encode(), "runtime-bootstrap")
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as stream:
            stream.write(sealed)
        print(json.dumps({"bootstrap_sealed": True}))
        return
    from config import DATABASE_URL
    if args.command == "migrate":
        if not args.confirm_offline:
            raise RuntimeError("Stop bot/API and supply --confirm-offline")
        source_url = os.environ.get("SOURCE_DATABASE_URL", "")
        if not source_url:
            raise RuntimeError("SOURCE_DATABASE_URL is required")
        storage = os.environ.get("SOURCE_STORAGE_DIR")
        result = await migrate_database(source_url, DATABASE_URL, Path(storage) if storage else None)
    else:
        engine = create_async_engine(DATABASE_URL, hide_parameters=True)
        try:
            async with engine.begin() as conn:
                result = {"encrypted_values": await verify_database(conn)}
        finally:
            await engine.dispose()
    print(json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate-key")
    generate.add_argument("--output", required=True)
    for command in ("seal-bootstrap", "seal-runtime"):
        commands.add_parser(command).add_argument("--output", required=True)
    for command in ("rotate-key", "export-active-key"):
        commands.add_parser(command).add_argument("--output", required=True)
    migrate = commands.add_parser("migrate")
    migrate.add_argument("--confirm-offline", action="store_true")
    commands.add_parser("verify")
    try:
        asyncio.run(main(parser.parse_args()))
    except Exception as error:
        # Operational output is safe even if a driver exception contains parameters.
        print(json.dumps({"ok": False, "error_type": type(error).__name__}))
        raise SystemExit(1)
