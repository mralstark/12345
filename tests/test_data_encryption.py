"""Exercise at-rest secrecy, authenticated failures, lookup joins and migration."""
import json
import base64
import os
import secrets
import subprocess
import sys
import asyncio
import threading
from datetime import date

import pytest
from sqlalchemy import BigInteger, Date, MetaData, Text, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from database.encrypted import EncryptedValue
from database.encryption_state import ensure_encryption_ready
from database.models import Base, Member, TelegramPhoneVerification, User
from scripts.data_encryption import migrate_database, verify_database
from utils.data_crypto import (
    FILE_PREFIX, DataEncryptionError, decrypt_bytes, decrypt_file_bytes,
    encrypt_bytes, encrypt_file_bytes, keyring,
)


def test_authenticated_random_text_and_deterministic_lookup():
    first = encrypt_bytes("Имя и телефон".encode(), "members.full_name")
    assert first != encrypt_bytes("Имя и телефон".encode(), "members.full_name")
    assert decrypt_bytes(first, "members.full_name").decode() == "Имя и телефон"
    with pytest.raises(DataEncryptionError):
        decrypt_bytes(first, "users.full_name")
    altered = first[:-5] + ("A" if first[-5] != "A" else "B") + first[-4:]
    with pytest.raises(DataEncryptionError):
        decrypt_bytes(altered, "members.full_name")
    with pytest.raises(DataEncryptionError):
        decrypt_bytes("legacy plaintext", "members.full_name")
    lookup = encrypt_bytes(b"123456", "identity.telegram", deterministic=True)
    assert lookup == encrypt_bytes(b"123456", "identity.telegram", deterministic=True)
    with pytest.raises(DataEncryptionError):
        decrypt_bytes(lookup, "identity.phone", deterministic=True)


def test_private_file_is_path_bound_and_has_no_plaintext_fallback():
    original = b"Synthetic private photo, name and phone"
    sealed = encrypt_file_bytes(original, "photos/1/sample.jpg")
    assert sealed.startswith(FILE_PREFIX) and original not in sealed
    assert decrypt_file_bytes(sealed, "photos/1/sample.jpg") == original
    for data, path in [(sealed, "photos/2/sample.jpg"), (original, "photos/1/sample.jpg"),
                       (sealed[:-1] + bytes([sealed[-1] ^ 1]), "photos/1/sample.jpg")]:
        with pytest.raises(DataEncryptionError):
            decrypt_file_bytes(data, path)


@pytest.mark.parametrize("mode", ["missing", "wrong"])
def test_missing_or_wrong_key_never_returns_plaintext(monkeypatch, tmp_path, mode):
    sealed = encrypt_bytes(b"private", "test")
    previous = keyring()
    path = tmp_path / "private-key.json"
    if mode == "wrong":
        path.write_text(json.dumps({"active": previous[0], "keys": {previous[0]: base64.urlsafe_b64encode(secrets.token_bytes(64)).decode()}}))
        path.chmod(0o600)
    monkeypatch.setenv("DATA_ENCRYPTION_KEY_FILE", str(path))
    keyring.cache_clear()
    try:
        with pytest.raises(DataEncryptionError):
            decrypt_bytes(sealed, "test")
    finally:
        monkeypatch.undo()
        keyring.cache_clear()
    assert keyring() == previous


async def test_raw_database_hides_values_but_lookup_join_and_dates_work(session, world):
    person = Member(region_id=world["moscow"].id, full_name="Тестовый Человек", phone="+79990000077", birth_date=date(2001, 2, 3))
    session.add(person)
    session.add(TelegramPhoneVerification(telegram_id=77777, phone="+79990000077"))
    session.add(User(telegram_id=77777, role="participant", full_name="Тестовый Человек", phone="+79990000077"))
    await session.commit()
    raw = (await session.execute(text("SELECT full_name,phone,birth_date FROM members WHERE id=:id"), {"id": person.id})).one()
    assert all(value.startswith("bdb1:") for value in raw)
    assert all("Человек" not in value and "+79990000077" not in value and "2001-02-03" not in value for value in raw)
    matched = (await session.execute(select(User).join(TelegramPhoneVerification, TelegramPhoneVerification.telegram_id == User.telegram_id)
                                    .where(TelegramPhoneVerification.phone == "+79990000077"))).scalar_one()
    assert matched.telegram_id == 77777
    await session.refresh(person)
    assert person.birth_date == date(2001, 2, 3)
    with pytest.raises(TypeError):
        select(Member).where(Member.full_name.ilike("%Человек%"))


async def test_legacy_copy_encrypts_from_first_target_insert_and_preserves_links(tmp_path, monkeypatch):
    import config
    import utils.private_files as private_files
    from database.models import Document

    source_url = "sqlite+aiosqlite:///" + (tmp_path / "source.db").as_posix()
    target_url = "sqlite+aiosqlite:///" + (tmp_path / "encrypted.db").as_posix()
    target_storage, source_storage = tmp_path / "sealed", tmp_path / "plain"
    source_storage.mkdir()
    (source_storage / "sample.txt").write_bytes(b"Synthetic private document")
    monkeypatch.setattr(config, "STORAGE_DIR", target_storage)
    monkeypatch.setattr(private_files, "STORAGE_DIR", target_storage)
    legacy = MetaData()
    for table in Base.metadata.tables.values():
        table.to_metadata(legacy)
    for table in legacy.tables.values():
        for column in table.columns:
            if isinstance(column.type, EncryptedValue):
                column.type = BigInteger() if column.type.kind == "int" else Date() if column.type.kind == "date" else Text()
    source = create_async_engine(source_url)
    try:
        async with source.begin() as conn:
            await conn.run_sync(legacy.create_all)
            await conn.execute(legacy.tables["regions"].insert().values(id=1, name="Регион", leader_user_id=2))
            await conn.execute(legacy.tables["members"].insert().values(id=1, region_id=1, full_name="Сохранённый Человек", birth_date=date(2002, 3, 4)))
            await conn.execute(legacy.tables["users"].insert().values(id=2, full_name="Сохранённый Человек", telegram_id=998877, role="leader", member_id=1))
            await conn.execute(legacy.tables["external_identities"].insert().values(id=1, provider="vk", subject="vk-only", user_id=2, full_name="VK участник"))
            await conn.execute(legacy.tables["membership_applications"].insert().values(id=1, region_id=1, full_name="VK заявитель", external_identity_id=1, telegram_id=None))
            await conn.execute(legacy.tables["documents"].insert().values(id=1, region_id=1, title="Документ", original_name="private.txt", stored_path="sample.txt", author_id=2))
        result = await migrate_database(source_url, target_url, source_storage)
        assert result["rows"]["users"] == 1 and result["encrypted_files"] == 1
        assert private_files.read_private_file(target_storage / "sample.txt") == b"Synthetic private document"
        assert (target_storage / "sample.txt").read_bytes().startswith(FILE_PREFIX)
        target = create_async_engine(target_url)
        try:
            async with target.begin() as conn:
                assert (await conn.execute(select(User.full_name).where(User.telegram_id == 998877))).scalar_one() == "Сохранённый Человек"
                assert (await conn.execute(select(User.member_id))).scalar_one() == 1
                assert (await conn.execute(select(Document.original_name))).scalar_one() == "private.txt"
                assert (await verify_database(conn))["members"] >= 2
                nested = await conn.begin_nested()
                try:
                    await conn.execute(text("UPDATE users SET full_name='plaintext downgrade'"))
                    with pytest.raises(DataEncryptionError):
                        await verify_database(conn)
                finally:
                    await nested.rollback()
        finally:
            await target.dispose()
        # Re-encryption rotates lookup ciphertext too, retaining plaintext identity.
        active, keys = keyring()
        rotation_path = tmp_path / "rotation.json"
        rotating = {name: base64.urlsafe_b64encode(key).decode() for name, key in keys.items()}
        rotating["next"] = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode()
        rotation_path.write_text(json.dumps({"active": "next", "keys": rotating}))
        rotation_path.chmod(0o600)
        old_location = os.environ["DATA_ENCRYPTION_KEY_FILE"]
        monkeypatch.setenv("DATA_ENCRYPTION_KEY_FILE", str(rotation_path))
        keyring.cache_clear()
        rotated_url = "sqlite+aiosqlite:///" + (tmp_path / "rotated.db").as_posix()
        monkeypatch.setattr(config, "STORAGE_DIR", tmp_path / "rotated-files")
        monkeypatch.setattr(private_files, "STORAGE_DIR", tmp_path / "rotated-files")
        try:
            rotated = await migrate_database(target_url, rotated_url, target_storage)
            assert rotated["rows"]["users"] == 1
            rotated_engine = create_async_engine(rotated_url)
            try:
                async with rotated_engine.begin() as conn:
                    assert (await conn.execute(select(User.id).where(User.telegram_id == 998877))).scalar_one() == 2
                    assert (await conn.execute(text("SELECT telegram_id FROM users"))).scalar_one().startswith("bdb1:next:")
                    await verify_database(conn)
            finally:
                await rotated_engine.dispose()
        finally:
            monkeypatch.setenv("DATA_ENCRYPTION_KEY_FILE", old_location)
            keyring.cache_clear()
        # The original source remains readable and untouched for a safe rollback.
        async with source.connect() as conn:
            assert (await conn.execute(text("SELECT full_name FROM users"))).scalar_one() == "Сохранённый Человек"
    finally:
        await source.dispose()


async def test_existing_database_without_migration_marker_cannot_start(session, world):
    connection = await session.connection()
    with pytest.raises(DataEncryptionError):
        await ensure_encryption_ready(connection)


def test_runtime_secrets_and_admin_settings_are_decrypted_without_environment_plaintext(tmp_path):
    from pathlib import Path
    private = {"BOT_TOKEN": "123456:synthetic-private-token-for-tests", "DATABASE_URL": "sqlite+aiosqlite:///" + (tmp_path / "unused.db").as_posix(),
               "MEDIA_SIGNING_KEY": "synthetic-signing-key-for-tests-only", "BOT_PROXY_URL": "", "VK_MINI_APP_SECRET": ""}
    bootstrap = {"SUPERUSER_TELEGRAM_IDS": [998877], "AUTO_FEDERAL_TELEGRAM_IDS": [], "PRIMARY_REVIEWER_FULL_NAME": "", "OPERATIONS_ALERT_TELEGRAM_ID": ""}
    env = dict(os.environ, APP_ENV="production", WEBAPP_URL="https://example.test", ALLOWED_HOSTS="example.test", MIN_FREE_STORAGE_MB="128")
    env.update({name: "" for name in private.keys() | bootstrap.keys()})
    env["DATA_RUNTIME_CIPHERTEXT"] = encrypt_bytes(json.dumps(private).encode(), "runtime-secrets")
    env["DATA_BOOTSTRAP_CIPHERTEXT"] = encrypt_bytes(json.dumps(bootstrap).encode(), "runtime-bootstrap")
    code = "import config; config.validate_security_config(); assert config.SUPERUSER_TELEGRAM_IDS=={998877}; assert config.BOT_TOKEN.startswith('123456:'); print('config-ok')"
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert result.stdout.strip() == b"config-ok"
    env["BOT_TOKEN"] = private["BOT_TOKEN"]
    failed = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, timeout=20)
    assert failed.returncode != 0 and private["BOT_TOKEN"].encode() not in failed.stderr


async def test_private_response_keeps_memory_budget_until_delivery_and_thread_completion(tmp_path, monkeypatch):
    import utils.private_files as private
    monkeypatch.setattr(private, "STORAGE_DIR", tmp_path)
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"synthetic stat-only fixture")
    started, finish = threading.Event(), threading.Event()

    def delayed_read(_):
        started.set()
        assert finish.wait(3)
        return b"synthetic decoded bytes"

    monkeypatch.setattr(private, "read_private_file", delayed_read)
    initial = private._reserved_response_bytes
    task = asyncio.create_task(private.private_file_response(path))
    for _ in range(100):
        if started.is_set():
            break
        await asyncio.sleep(.01)
    assert started.is_set() and private._reserved_response_bytes > initial
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert private._reserved_response_bytes > initial
    finish.set()
    for _ in range(100):
        if private._reserved_response_bytes == initial:
            break
        await asyncio.sleep(.01)
    assert private._reserved_response_bytes == initial

    monkeypatch.setattr(private, "read_private_file", lambda _: b"synthetic")
    response = await private.private_file_response(path)
    assert private._reserved_response_bytes > initial

    async def disconnected(_):
        raise ConnectionError("synthetic client disconnect")

    with pytest.raises(ConnectionError):
        await response({"type": "http"}, None, disconnected)
    assert private._reserved_response_bytes == initial
