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
    finished_at: float | None = None


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

        # Browser sessions must never share another user's cached mutation.
        actor = hashlib.sha256((request.headers.get("Authorization", "") + "\0" +
                                request.headers.get("Cookie", "")).encode()).hexdigest()
        cache_key = (actor, request.method.upper(), request.url.path, raw_key)
        async with self._lock:
            cutoff = monotonic() - self.ttl_seconds
            for key in list(self._entries):
                cached = self._entries[key]
                if cached.ready.is_set() and cached.finished_at is not None and cached.finished_at < cutoff:
                    self._entries.pop(key, None)
            entry = self._entries.get(cache_key)
            owner = entry is None
            if owner:
                # An upload may outlive the cache TTL. Never evict its running
                # operation, either by time or to admit a flood of new keys.
                while len(self._entries) >= self.max_entries:
                    completed = next((k for k, v in self._entries.items() if v.ready.is_set()), None)
                    if completed is None:
                        return JSONResponse(status_code=503, content={"detail": "Сервер занят. Повторите запрос через несколько секунд"}, headers={"Retry-After": "2"})
                    self._entries.pop(completed)
                entry = _Entry()
                self._entries[cache_key] = entry
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
            if response.status_code == 429 or response.status_code >= 500:
                # A retry after Retry-After must be allowed to run, rather
                # than replaying a temporary decoder/rate-limit failure.
                async with self._lock:
                    if self._entries.get(cache_key) is entry:
                        self._entries.pop(cache_key)
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
            entry.finished_at = monotonic()
            entry.ready.set()


idempotency_store = IdempotencyStore()
