"""Browser sessions confirmed explicitly in the Telegram bot.

Only hashes of the independent browser secret and approval token are stored.
Opening a deep link does not approve a login; the user must confirm it.
"""
import hashlib
import secrets
import time

from fastapi import HTTPException, Request
from sqlalchemy import delete, select, update

from config import IS_PRODUCTION, WEBAPP_URL
from database.db import async_session
from database.models import BrowserSession, User

COOKIE_NAME = "__Host-bratstvo_session" if IS_PRODUCTION else "bratstvo_session"
APPROVAL_TTL = 300
SESSION_TTL = 12 * 60 * 60


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def same_origin(request: Request) -> None:
    from urllib.parse import urlsplit
    expected = urlsplit(WEBAPP_URL or str(request.base_url))
    origin = request.headers.get("origin", "")
    if origin != f"{expected.scheme}://{expected.netloc}":
        raise HTTPException(403, "Запрос должен быть отправлен из кабинета")


async def create_session() -> tuple[str, str, str]:
    secret, grant = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    code = f"{secrets.randbelow(1_000_000):06d}"
    now = int(time.time())
    async with async_session() as session:
        await session.execute(delete(BrowserSession).where(BrowserSession.expires_at <= now))
        session.add(BrowserSession(
            secret_hash=digest(secret), grant_hash=digest(grant), code=code,
            expires_at=now + APPROVAL_TTL,
        ))
        await session.commit()
    return secret, grant, code


async def pending_code(grant: str) -> str | None:
    if len(grant) != 43:
        return None
    async with async_session() as session:
        row = (await session.execute(select(BrowserSession).where(
            BrowserSession.grant_hash == digest(grant),
            BrowserSession.user_id.is_(None), BrowserSession.expires_at > int(time.time()),
        ))).scalar_one_or_none()
        return row.code if row else None


async def approve(grant: str, user: User) -> bool:
    if len(grant) != 43 or not user.is_active or getattr(user, "_impersonated_by", None):
        return False
    now = int(time.time())
    async with async_session() as session:
        result = await session.execute(update(BrowserSession).where(
            BrowserSession.grant_hash == digest(grant),
            BrowserSession.user_id.is_(None), BrowserSession.expires_at > now,
        ).values(user_id=user.id, grant_hash=None, expires_at=now + SESSION_TTL))
        await session.commit()
        return result.rowcount == 1


async def session_user(request: Request) -> User | None:
    secret = request.cookies.get(COOKIE_NAME, "")
    if len(secret) != 43:
        return None
    async with async_session() as session:
        user = (await session.execute(select(User).join(
            BrowserSession, BrowserSession.user_id == User.id,
        ).where(BrowserSession.secret_hash == digest(secret),
                BrowserSession.expires_at > int(time.time()), User.is_active.is_(True)))).scalar_one_or_none()
        if user is not None:
            session.expunge(user)
        return user
