"""Небольшой process-local limiter для одного uvicorn-процесса.

Nginx ограничивает IP до разбора запроса, а этот слой — уже проверенный
Telegram ID. Текущий production запускает один процесс API, поэтому общее
хранилище или отдельный сервис для счётчиков не требуется.
"""

from collections import defaultdict, deque
from time import monotonic

from fastapi import HTTPException, status


class SlidingWindowLimiter:
    def __init__(self, max_keys: int = 20_000) -> None:
        self._events: dict[tuple[str, int], deque[float]] = defaultdict(deque)
        self._max_keys = max_keys

    def check(self, scope: str, subject: int, limit: int, window_seconds: int) -> int | None:
        now = monotonic()
        key = (scope, subject)
        events = self._events[key]
        cutoff = now - window_seconds
        while events and events[0] <= cutoff:
            events.popleft()
        if len(events) >= limit:
            return max(1, int(events[0] + window_seconds - now) + 1)
        events.append(now)

        # Не даём словарю бесконечно расти при потоке новых аккаунтов.
        if len(self._events) > self._max_keys:
            stale = [stored_key for stored_key, values in self._events.items() if not values or values[-1] <= cutoff]
            for stored_key in stale:
                self._events.pop(stored_key, None)
        return None


_limiter = SlidingWindowLimiter()


def enforce_user_rate_limit(telegram_id: int, method: str, path: str) -> None:
    method = method.upper()
    if "export" in path or path.startswith("/api/reports"):
        scope, limit, window = "export", 6, 60
    elif method in {"GET", "HEAD", "OPTIONS"}:
        scope, limit, window = "read", 240, 60
    elif path.startswith("/api/register/"):
        scope, limit, window = "registration", 5, 60
    elif path == "/api/documents" or path.endswith("/avatar") or path == "/api/news":
        scope, limit, window = "upload", 12, 60
    else:
        scope, limit, window = "write", 60, 60

    retry_after = _limiter.check(scope, telegram_id, limit, window)
    if retry_after is None and scope == "registration":
        # Ограничивает поток заявок с множества Telegram-аккаунтов. Production
        # использует один API-процесс, поэтому счётчик охватывает весь сервис.
        retry_after = _limiter.check("registration-global", 0, 60, 60)
    if retry_after is None and scope == "upload":
        retry_after = _limiter.check("upload-global", 0, 60, 60)
    if retry_after is not None:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Слишком много запросов. Повторите позже.",
            headers={"Retry-After": str(retry_after)},
        )
