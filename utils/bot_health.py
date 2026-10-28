"""Private operational counters. Never persist updates, chat IDs or payloads."""
import asyncio
from contextvars import ContextVar
import json
import logging
import math
import os
from pathlib import Path
import stat
import tempfile
import time

from aiogram import BaseMiddleware
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramConflictError, TelegramNetworkError, TelegramUnauthorizedError
from aiogram.methods import GetUpdates, SendMessage

HEARTBEAT_INTERVAL = 10
HEARTBEAT_MAX_AGE = 45
POLL_MAX_AGE = 180
_update_context = ContextVar("bot_health_update", default=None)
logger = logging.getLogger(__name__)


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise ValueError("Operational state directory must not be a symlink")
    fd, name = tempfile.mkstemp(prefix=".health-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, allow_nan=False)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


class BotHealth:
    def __init__(self, path):
        self.path = Path(path)
        self.data = {
            "version": 1, "pid": os.getpid(), "invocation_id": os.getenv("INVOCATION_ID", ""),
            "started_at": time.time(), "phase": "starting", "updated_at": 0,
            "last_poll_started_at": None, "last_poll_success_at": None,
            "last_poll_error_at": None, "poll_error_kind": None,
            "poll_successes": 0, "poll_failures": 0, "updates_received": 0,
            "updates_processed": 0, "handler_failures": 0, "last_roundtrip_at": None,
            "last_send_success_at": None, "send_failures": 0,
            "setup_pending": True, "setup_errors": [], "notifier_running": False,
        }

    def write(self):
        self.data["updated_at"] = time.time()
        atomic_json(self.path, self.data)

    async def heartbeat(self):
        while True:
            try:
                self.write()
            except (OSError, ValueError) as exc:
                logger.error("bot-health-write-failed %s", type(exc).__name__)
            await asyncio.sleep(HEARTBEAT_INTERVAL)


class TelegramHealthMiddleware(BaseRequestMiddleware):
    def __init__(self, health):
        self.health = health

    async def __call__(self, make_request, bot, method):
        state = self.health.data
        polling = isinstance(method, GetUpdates)
        if polling:
            state.update(phase="polling", last_poll_started_at=time.time())
        try:
            result = await make_request(bot, method)
        except Exception as exc:
            if polling:
                kind = ("network" if isinstance(exc, TelegramNetworkError) else
                        "conflict" if isinstance(exc, TelegramConflictError) else
                        "credentials" if isinstance(exc, TelegramUnauthorizedError) else "api")
                state.update(last_poll_error_at=time.time(), poll_error_kind=kind)
                state["poll_failures"] += 1
            elif isinstance(method, SendMessage):
                state["send_failures"] += 1
            raise
        if polling:
            # An empty long-poll response proves the real receiving path works.
            state.update(last_poll_success_at=time.time(), poll_error_kind=None)
            state["poll_successes"] += 1
            state["updates_received"] += len(result)
        elif isinstance(method, SendMessage):
            state["last_send_success_at"] = time.time()
            context = _update_context.get()
            if context is not None:
                context["replied"] = True
        return result


class UpdateHealthMiddleware(BaseMiddleware):
    def __init__(self, health):
        self.health = health

    async def __call__(self, handler, event, data):
        context = {"replied": False}
        token = _update_context.set(context)
        try:
            result = await handler(event, data)
            self.health.data["updates_processed"] += 1
            if context["replied"]:
                self.health.data["last_roundtrip_at"] = time.time()
            return result
        except Exception:
            self.health.data["handler_failures"] += 1
            raise
        finally:
            _update_context.reset(token)


def read_health(path, expected_pid, expected_invocation, expected_uid=None):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(descriptor, "r") as stream:
        metadata = os.fstat(stream.fileno())
        if (not stat.S_ISREG(metadata.st_mode) or
                os.name == "posix" and (metadata.st_mode & 0o077 or
                                       expected_uid is not None and metadata.st_uid != expected_uid)):
            raise ValueError("Operational state must be private")
        raw = stream.read(16385)
    if len(raw) > 16384:
        raise ValueError("Operational state is too large")
    data = json.loads(raw)
    if (not isinstance(data, dict) or data.get("version") != 1 or
            not expected_pid or not expected_invocation or data.get("pid") != expected_pid or
            data.get("invocation_id") != expected_invocation):
        raise ValueError("Operational state belongs to another service instance")
    return data


def readiness_issues(data, now=None):
    now = time.time() if now is None else now
    def age(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return float("inf")
        return now - value if value <= now + 5 else float("inf")

    issues = []
    if age(data.get("updated_at")) > HEARTBEAT_MAX_AGE:
        issues.append("bot-heartbeat-stale")
    if data.get("phase") != "polling" or data.get("last_poll_success_at") is None:
        issues.append("bot-polling-not-ready")
    elif age(data["last_poll_success_at"]) > POLL_MAX_AGE:
        issues.append("bot-polling-stale")
    if data.get("poll_error_kind") in ("credentials", "conflict"):
        issues.append("bot-polling-" + data["poll_error_kind"])
    if data.get("setup_errors") or data.get("setup_pending") and age(data.get("started_at")) > 600:
        issues.append("bot-configuration")
    if data.get("phase") == "polling" and not data.get("notifier_running"):
        issues.append("bot-notifier-stopped")
    return issues
