"""Same-origin browser login, approved in the bot, without passwords."""
import asyncio
from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import delete

from api.rate_limit import _limiter
from config import IS_PRODUCTION
from database.db import async_session
from database.models import BrowserSession
from services.browser_login import (
    COOKIE_NAME, SESSION_TTL, create_session, digest, same_origin, session_user,
)
from utils.notify import get_notifier_bot

router = APIRouter(prefix="/auth/browser", tags=["browser login"])


@router.post("/start")
async def start(request: Request, response: Response) -> dict:
    same_origin(request)
    # The public endpoint must also be bounded before there is a Telegram ID.
    retry = _limiter.check("browser-login", request.client.host if request.client else "", 5, 60)
    retry = retry or _limiter.check("browser-login-global", 0, 60, 60)
    if retry:
        raise HTTPException(429, "Подождите минуту перед повторным входом")
    bot = get_notifier_bot()
    if bot is None:
        raise HTTPException(503, "Вход временно недоступен")
    try:
        me = await asyncio.wait_for(bot.get_me(), timeout=10)
    except Exception:
        raise HTTPException(503, "Нет связи с Telegram. Попробуйте позже") from None
    secret, grant, code = await create_session()
    response.set_cookie(COOKIE_NAME, secret, max_age=SESSION_TTL, httponly=True,
                        secure=IS_PRODUCTION, samesite="strict", path="/")
    return {"url": f"https://t.me/{me.username}?start=web_{grant}", "code": code, "expires_in": 300}


@router.get("/status")
async def status(request: Request) -> dict:
    retry = _limiter.check("browser-status", request.client.host if request.client else "", 120, 60)
    if retry:
        raise HTTPException(429, "Слишком много проверок входа")
    return {"authenticated": await session_user(request) is not None}


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict:
    same_origin(request)
    async with async_session() as session:
        await session.execute(delete(BrowserSession).where(
            BrowserSession.secret_hash == digest(request.cookies.get(COOKIE_NAME, "")),
        ))
        await session.commit()
    response.delete_cookie(COOKIE_NAME, path="/", secure=IS_PRODUCTION, httponly=True, samesite="strict")
    return {"ok": True}
