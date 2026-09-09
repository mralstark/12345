"""Авторизация Mini App через подписанные Telegram initData (ТЗ §2).

Отдельного логина и пароля нет: клиент присылает строку initData, которую ему
выдал Telegram, в заголовке `Authorization: tma <initData>`. Подпись проверяется
секретом, производным от токена бота, — подделать её, не зная токена, нельзя.
"""

import hashlib
import hmac
import logging
import os
import time
from urllib.parse import parse_qsl

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from config import BOT_TOKEN
from database.db import async_session
from database.models import User
from utils.users import resolve_user

logger = logging.getLogger(__name__)

# initData считается протухшей через сутки — столько же живёт открытая вкладка Mini App.
MAX_AUTH_AGE_SECONDS = 24 * 60 * 60

# Только для локальной разработки интерфейса без Telegram: подставляет указанный
# telegram_id вместо проверки подписи. В проде переменная должна быть пустой.
DEV_TELEGRAM_ID = os.getenv("DEV_TELEGRAM_ID", "").strip()


class InitDataError(Exception):
    pass


def validate_init_data(init_data: str, bot_token: str, max_age: int = MAX_AUTH_AGE_SECONDS) -> dict:
    """Проверяет подпись initData и возвращает разобранные поля.
    Алгоритм из документации Telegram: secret = HMAC(«WebAppData», token),
    подпись = HMAC(secret, строка «ключ=значение», отсортированная по ключу)."""
    if not init_data:
        raise InitDataError("Пустые initData")

    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise InitDataError("В initData нет подписи")

    data_check_string = "\n".join(f"{key}={pairs[key]}" for key in sorted(pairs))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed, received_hash):
        raise InitDataError("Подпись initData не сходится")

    auth_date = int(pairs.get("auth_date", "0"))
    if max_age and (time.time() - auth_date) > max_age:
        raise InitDataError("initData устарели, переоткройте кабинет")

    return pairs


def _parse_user_field(raw_user: str) -> dict:
    import json

    try:
        return json.loads(raw_user)
    except (ValueError, TypeError) as exc:
        raise InitDataError("Не удалось разобрать поле user") from exc


async def get_current_user(authorization: str = Header(default="")) -> User:
    """Пользователь Mini App. Регионы и права дальше считает utils/access.py."""
    telegram_id: int
    full_name = ""

    if DEV_TELEGRAM_ID:
        # Режим локальной отладки — включается только явной переменной окружения.
        telegram_id = int(DEV_TELEGRAM_ID)
    else:
        scheme, _, raw = authorization.partition(" ")
        if scheme.lower() != "tma" or not raw:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Нужен заголовок Authorization: tma <initData>")
        if not BOT_TOKEN:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "BOT_TOKEN не задан на сервере")

        try:
            data = validate_init_data(raw, BOT_TOKEN)
            user_field = _parse_user_field(data.get("user", ""))
        except InitDataError as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc

        telegram_id = int(user_field.get("id", 0))
        full_name = " ".join(
            part for part in (user_field.get("first_name"), user_field.get("last_name")) if part
        )

    async with async_session() as session:
        user = await resolve_user(session, telegram_id, full_name)
        if user is None or not user.is_active:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Доступ не открыт. Напишите боту /start — там либо ссылка на подачу заявки, "
                "либо создание личного кабинета, если вы уже приняты.",
            )
        session.expunge(user)
        return user


class TelegramIdentity:
    """Личность из initData без резолва в существующего User — для формы
    регистрации (api/routers/register.py), где человека ещё нет в системе.
    В остальном — тот же разбор, что и get_current_user."""

    def __init__(self, telegram_id: int, full_name: str) -> None:
        self.telegram_id = telegram_id
        self.full_name = full_name


async def get_telegram_identity(authorization: str = Header(default="")) -> TelegramIdentity:
    if DEV_TELEGRAM_ID:
        return TelegramIdentity(int(DEV_TELEGRAM_ID), "")

    scheme, _, raw = authorization.partition(" ")
    if scheme.lower() != "tma" or not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Нужен заголовок Authorization: tma <initData>")
    if not BOT_TOKEN:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "BOT_TOKEN не задан на сервере")

    try:
        data = validate_init_data(raw, BOT_TOKEN)
        user_field = _parse_user_field(data.get("user", ""))
    except InitDataError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc

    telegram_id = int(user_field.get("id", 0))
    full_name = " ".join(
        part for part in (user_field.get("first_name"), user_field.get("last_name")) if part
    )
    return TelegramIdentity(telegram_id, full_name)


async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session


CurrentUser = Depends(get_current_user)
Db = Depends(get_db)
