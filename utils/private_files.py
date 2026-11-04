"""Private media/export encryption; plaintext exists only in bounded response memory."""
import asyncio
import mimetypes
import os
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException, Response

from config import MAX_UPLOAD_BYTES, STORAGE_DIR
from utils.data_crypto import DataEncryptionError, decrypt_file_bytes, encrypt_file_bytes
from utils.storage import ensure_storage_capacity

MAX_PRIVATE_BYTES = max(MAX_UPLOAD_BYTES, 20 * 1024 * 1024)
RESPONSE_MEMORY_BUDGET = 128 * 1024 * 1024
_reserved_response_bytes = 0
_responses_in_flight = 0


def _release_response(reservation):
    global _reserved_response_bytes, _responses_in_flight
    _reserved_response_bytes -= reservation
    _responses_in_flight -= 1


class PrivateResponse(Response):
    def __init__(self, payload, reservation, **kwargs):
        self.reservation = reservation
        super().__init__(payload, **kwargs)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.body = b""
            if self.reservation:
                _release_response(self.reservation)
                self.reservation = 0


def private_path(path: Path) -> tuple[Path, str]:
    root = STORAGE_DIR.resolve()
    resolved = path.resolve()
    if path.is_symlink() or not resolved.is_relative_to(root) or resolved == root:
        raise DataEncryptionError("Private file path is outside storage")
    return resolved, resolved.relative_to(root).as_posix()


def write_private_file(path: Path, payload: bytes, *, exclusive: bool = True) -> None:
    if len(payload) > MAX_PRIVATE_BYTES:
        raise DataEncryptionError("Private file exceeds the configured size limit")
    path, relative = private_path(path)
    sealed = encrypt_file_bytes(payload, relative)
    ensure_storage_capacity(len(sealed))
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(sealed)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        if exclusive:
            path.unlink(missing_ok=True)
        raise


def read_private_file(path: Path) -> bytes:
    path, relative = private_path(path)
    if path.stat().st_size > MAX_PRIVATE_BYTES + 128:
        raise DataEncryptionError("Private file exceeds the configured size limit")
    with path.open("rb") as stream:
        payload = stream.read(MAX_PRIVATE_BYTES + 129)
    if len(payload) > MAX_PRIVATE_BYTES + 128:
        raise DataEncryptionError("Private file exceeds the configured size limit")
    return decrypt_file_bytes(payload, relative)


async def private_file_response(path: Path, *, media_type: str | None = None,
                                filename: str | None = None, headers: dict | None = None) -> Response:
    global _reserved_response_bytes, _responses_in_flight
    # Authenticate before HTTP headers. Keep the reservation until delivery ends,
    # so slow readers cannot accumulate unbounded decrypted response buffers.
    path, _ = private_path(path)
    size = path.stat().st_size
    if size > MAX_PRIVATE_BYTES + 128:
        raise DataEncryptionError("Private file exceeds the configured size limit")
    reservation = 4 * size + 256 * 1024
    if _reserved_response_bytes + reservation > RESPONSE_MEMORY_BUDGET or _responses_in_flight >= 256:
        raise HTTPException(429, "Сейчас скачивается много файлов. Повторите через несколько секунд", headers={"Retry-After": "2"})
    _reserved_response_bytes += reservation
    _responses_in_flight += 1
    loading = asyncio.create_task(asyncio.to_thread(read_private_file, path))
    try:
        payload = await asyncio.shield(loading)
    except BaseException:
        if loading.done():
            _release_response(reservation)
        else:
            # Cancelling the HTTP request does not stop a Python I/O/crypto thread.
            # Hold its budget until that thread has actually completed.
            def completed(task):
                if not task.cancelled():
                    task.exception()
                _release_response(reservation)
            loading.add_done_callback(completed)
        raise
    try:
        response_headers = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff", **(headers or {})}
        if filename and "Content-Disposition" not in response_headers:
            response_headers["Content-Disposition"] = "attachment; filename*=utf-8''" + quote(filename, safe="")
        raster_types = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}
        return PrivateResponse(payload, reservation, media_type=media_type or raster_types.get(path.suffix.lower()) or mimetypes.guess_type(str(path))[0] or "application/octet-stream",
                               headers=response_headers)
    except BaseException:
        _release_response(reservation)
        raise
