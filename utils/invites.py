"""Код в deep-link'е /start для публичной саморегистрации.

APPLY_XXXX — многоразовый код региона на форму заявки на вступление
(Region.application_code) — им может воспользоваться сколько угодно раз
разных людей, руководитель может перевыпустить при утечке.

Deep-link-параметр Telegram ограничен 64 символами и алфавитом [A-Za-z0-9_-],
поэтому код — случайные символы из безопасного алфавита без похожих друг на
друга (0/O, 1/I/l).
"""

import secrets

APPLY_PREFIX = "APPLY_"
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_LENGTH = 12


def generate_application_code() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LENGTH))


def application_link(bot_username: str, code: str) -> str:
    return f"https://t.me/{bot_username}?start={APPLY_PREFIX}{code}"


def parse_application_payload(payload: str | None) -> str | None:
    """Достаёт код региона из payload /start. None, если это не ссылка заявки."""
    if not payload:
        return None
    payload = payload.strip()
    if not payload.startswith(APPLY_PREFIX):
        return None
    code = payload[len(APPLY_PREFIX) :].strip().upper()
    return code or None
