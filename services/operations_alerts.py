"""Opt-in operator alerts; persist pending incidents when Telegram is unavailable."""
import asyncio
import json
import logging
from pathlib import Path
import time

from utils.bot_health import atomic_json

REPEAT_SECONDS = 1800
logger = logging.getLogger(__name__)


async def send_alert(recipient, text):
    from utils.notify import get_notifier_bot, close_notifier_bot
    try:
        bot = get_notifier_bot()
        if bot is None:
            return False
        await asyncio.wait_for(bot.send_message(recipient, text, request_timeout=8), timeout=10)
        return True
    finally:
        await close_notifier_bot()


async def alert_health(directory, state, recipient, *, now=None, sender=None):
    # A selected role, first superuser or previous contact is not authorization.
    if not recipient:
        return False
    digits = str(recipient).removeprefix("-")
    if not digits.isascii() or not digits.isdigit() or not 1 <= len(digits) <= 19 or not 0 < int(digits) < 2**63:
        logger.error("operations-alert-recipient-invalid")
        return False
    now = time.time() if now is None else now
    path = Path(directory) / "alert-state.json"
    previous = {}
    try:
        if path.exists():
            if path.is_symlink() or path.stat().st_size > 16384:
                raise ValueError("Invalid alert state")
            previous = json.loads(path.read_text())
            if not isinstance(previous, dict):
                raise ValueError("Invalid alert state")
    except (ValueError, OSError):
        logger.error("operations-alert-state-invalid")
        return False
    issues = sorted(set(state["issues"]))
    if issues and state.get("consecutive_failures", 0) < 2:
        return False  # Avoid alerting on a short deployment/restart.
    if issues:
        previous["pending_issues"] = issues
    elif not previous.get("last_sent_issues") and not previous.get("pending_issues"):
        return False
    if (previous.get("last_attempt_issues") == issues and
            now - previous.get("last_attempt_at", 0) < REPEAT_SECONDS):
        atomic_json(path, previous)
        return False
    previous.update(last_attempt_at=now, last_attempt_issues=issues)
    # Save before the send; an uncertain response must not cause a fast retry.
    atomic_json(path, previous)
    incident = issues or previous.get("pending_issues") or previous.get("last_sent_issues") or []
    text = ("Сервис Братства: обнаружен сбой." if issues else "Сервис Братства: работа восстановлена.")
    text += "\nПроверки: " + ", ".join(incident)
    try:
        sent = await (sender or send_alert)(int(recipient), text)
    except Exception as exc:
        logger.warning("operations-alert-delivery-failed %s", type(exc).__name__)
        return False
    if sent:
        previous.update(last_sent_at=now, last_sent_issues=issues)
        if not issues:
            previous["pending_issues"] = []
        atomic_json(path, previous)
    return bool(sent)
