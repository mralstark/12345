"""Короткий серверный кэш результатов для повторных изменяющих запросов."""

import asyncio
import hashlib
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from time import monotonic

from fastapi import Request
from fastapi.responses import JSONResponse, Response


_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")


@dataclass
class _Entry:
    created_at: float = field(default_factory=monotonic)
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    status_code: int | None = None
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    error: BaseException | None = None


class IdempotencyStore:
    def __init__(self, ttl_seconds: int = 30, max_entries: int = 5_000) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._entries: OrderedDict[tuple[str, str, str, str], _Entry] = OrderedDict()
        self._lock = asyncio.Lock()

    async def execute(self, request: Request, call_next) -> Response:
        if request.method.upper() not in {"POST", "PUT", "PATCH", "DELETE"}:
            return await call_next(request)
        raw_key = request.headers.get("Idempotency-Key", "")
        if not raw_key:
            return await call_next(request)
        if not _KEY_RE.fullmatch(raw_key):
            return JSONResponse(status_code=400, content={"detail": "Некорректный ключ идемпотентности"})

        actor = hashlib.sha256(request.headers.get("Authorization", "").encode()).hexdigest()
        cache_key = (actor, request.method.upper(), request.url.path, raw_key)
        async with self._lock:
            cutoff = monotonic() - self.ttl_seconds
            for key in list(self._entries):
                if self._entries[key].created_at >= cutoff:
                    break
                self._entries.pop(key, None)
            entry = self._entries.get(cache_key)
            owner = entry is None
            if owner:
                entry = _Entry()
                self._entries[cache_key] = entry
                while len(self._entries) > self.max_entries:
                    self._entries.popitem(last=False)
            else:
                self._entries.move_to_end(cache_key)

        if not owner:
            await entry.ready.wait()
            if entry.error is not None:
                raise entry.error
            return Response(content=entry.body, status_code=entry.status_code or 500, headers=entry.headers)

        try:
            response = await call_next(request)
            body = b"".join([chunk async for chunk in response.body_iterator])
            entry.status_code = response.status_code
            entry.body = body
            entry.headers = dict(response.headers)
            return Response(
                content=body,
                status_code=response.status_code,
                headers=entry.headers,
                background=response.background,
            )
        except BaseException as exc:
            entry.error = exc
            async with self._lock:
                self._entries.pop(cache_key, None)
            raise
        finally:
            entry.ready.set()


idempotency_store = IdempotencyStore()
