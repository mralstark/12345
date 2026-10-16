"""Short-lived private export files for Telegram's native download dialog.

The signed capability contains no Telegram credentials. Permissions and account
activity are checked again on download. Files expire in ten minutes; disk usage
and generation concurrency are bounded.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from pathlib import Path

from fastapi import HTTPException

from config import BOT_TOKEN, MEDIA_SIGNING_KEY, STORAGE_DIR
from utils.storage import ensure_storage_capacity

TTL = 600
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_CACHE_BYTES = 256 * 1024 * 1024
generation_slots = asyncio.Semaphore(2)
_cache_lock = asyncio.Lock()


def _signature(raw: str) -> str:
    key = MEDIA_SIGNING_KEY or BOT_TOKEN
    if not key:
        raise RuntimeError("Ключ подписи не настроен")
    return hmac.new(key.encode(), ("report-download:" + raw).encode(), hashlib.sha256).hexdigest()


def decode_ticket(ticket: str) -> dict:
    try:
        if len(ticket) > 2048:
            raise ValueError
        raw, signature = ticket.split(".")
        if not hmac.compare_digest(signature, _signature(raw)):
            raise ValueError
        data = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        if (data["exp"] <= time.time() or data["exp"] > time.time() + TTL + 5 or
                not re.fullmatch(r"[a-f0-9]{48}\.(xlsx|pdf|csv|docx|pptx|txt)", data["file"])):
            raise ValueError
        return data
    except (ValueError, TypeError, KeyError):
        raise HTTPException(403, "Ссылка устарела. Сформируйте отчёт заново") from None


def cache_dir() -> Path:
    return STORAGE_DIR / "report_downloads"


async def save_export(payload: bytes, user_id: int, region_id: int, format: str, filename: str,
                      *, kind: str = "report", cell_id: int | None = None,
                      document_id: int | None = None) -> str:
    if len(payload) > MAX_FILE_BYTES:
        raise HTTPException(413, "Отчёт слишком большой. Выберите более короткий период")
    async with _cache_lock:
        directory = cache_dir()
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        now = time.time()
        used = 0
        for file in directory.iterdir():
            if not file.is_file():
                continue
            if file.stat().st_mtime < now - TTL:
                file.unlink(missing_ok=True)
            else:
                used += file.stat().st_size
        if used + len(payload) > MAX_CACHE_BYTES:
            raise HTTPException(503, "Сейчас формируется много отчётов. Попробуйте через несколько минут")
        try:
            ensure_storage_capacity(len(payload))
        except ValueError as exc:
            raise HTTPException(507, str(exc)) from exc
        file_id = secrets.token_hex(24) + "." + format
        path = directory / file_id
        with path.open("xb") as stream:
            stream.write(payload)
        path.chmod(0o600)
        data = {"uid": user_id, "rid": region_id, "file": file_id, "name": filename, "exp": int(now) + TTL,
                "kind": kind, "cell_id": cell_id, "document_id": document_id}
        raw = base64.urlsafe_b64encode(json.dumps(data, ensure_ascii=False).encode()).decode().rstrip("=")
        return raw + "." + _signature(raw)
