"""Signed VK launches and explicitly confirmed linking, without browser cookies."""
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select

from api.auth import get_current_user
from api.rate_limit import enforce_user_rate_limit
from api.routers.vk_login import limit, require_fresh_telegram
from database.db import async_session
from database.models import ExternalIdentity, OAuthLoginAttempt, User
from services import vk_mini
from services.browser_login import digest

router = APIRouter(prefix="/auth/vk-mini", tags=["VK Mini App"])


class StartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    full_name: str = Field(default="", max_length=128)
    link_token: str | None = Field(default=None, min_length=43, max_length=43, pattern=r"^[A-Za-z0-9_-]+$")


class PhoneIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phone_number: str = Field(min_length=5, max_length=32)
    sign: str = Field(default="", max_length=256)


class LinkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    link_token: str = Field(min_length=43, max_length=43, pattern=r"^[A-Za-z0-9_-]+$")


@router.post("/link-preview")
async def link_preview(payload: LinkIn, request: Request, authorization: str = Header(default="")):
    limit(request, "vk-mini-preview", 15)
    vk_mini.launch_from_header(authorization)
    async with async_session() as session:
        user = (await session.execute(select(User).join(
            OAuthLoginAttempt, OAuthLoginAttempt.user_id == User.id,
        ).where(
            OAuthLoginAttempt.state_hash == digest(payload.link_token),
            OAuthLoginAttempt.purpose == "mini_link",
            OAuthLoginAttempt.binding_hash == digest(vk_mini.VK_MINI_APP_ID),
            OAuthLoginAttempt.expires_at > int(time.time()), User.is_active.is_(True),
            User.view_as_user_id.is_(None),
        ))).scalar_one_or_none()
        if user is None:
            raise HTTPException(409, "Ссылка подключения использована или устарела. Создайте новую в Telegram")
        return {"full_name": user.full_name}


@router.post("/start")
async def start(payload: StartIn, request: Request, authorization: str = Header(default="")):
    limit(request, "vk-mini-start", 120, global_budget=600)
    launch = vk_mini.launch_from_header(authorization)
    enforce_user_rate_limit("vkmini:" + launch["subject"], "POST", request.url.path)
    return await vk_mini.start(launch, " ".join(payload.full_name.split()), payload.link_token)


@router.post("/phone")
async def phone(payload: PhoneIn, request: Request, authorization: str = Header(default="")):
    limit(request, "vk-mini-phone", 30, global_budget=300)
    launch = vk_mini.launch_from_header(authorization)
    enforce_user_rate_limit("vkmini:" + launch["subject"], "POST", request.url.path)
    number = vk_mini.verify_phone(launch, payload.phone_number, payload.sign)
    async with async_session() as session:
        identity = await vk_mini.require_identity(session, launch)
        identity.verified_phone = number
        await session.commit()
    return {"verified": True, "phone": number}


@router.post("/link")
async def link(request: Request, user: User = Depends(get_current_user)):
    require_fresh_telegram(request, user)
    limit(request, "vk-mini-link")
    if not vk_mini.enabled():
        raise HTTPException(503, "Подключение VK Mini App ещё не завершено")
    return await vk_mini.create_link(user.id)


@router.get("/account")
async def account(request: Request, user: User = Depends(get_current_user)):
    async with async_session() as session:
        identity = (await session.execute(select(ExternalIdentity.id).where(
            ExternalIdentity.provider == vk_mini.PROVIDER, ExternalIdentity.user_id == user.id,
        ))).scalar_one_or_none()
    can_link = bool(user.telegram_id is not None and request.headers.get("authorization", "").lower().startswith("tma ")
                    and not getattr(user, "_impersonated_by", None))
    return {"enabled": vk_mini.enabled(), "connected": identity is not None,
            "can_link": can_link, "can_unlink": can_link and identity is not None}


@router.delete("/link")
async def unlink(request: Request, user: User = Depends(get_current_user)):
    require_fresh_telegram(request, user)
    async with async_session() as session:
        identity = (await session.execute(select(ExternalIdentity).where(
            ExternalIdentity.provider == vk_mini.PROVIDER, ExternalIdentity.user_id == user.id,
        ).with_for_update())).scalar_one_or_none()
        if identity is not None:
            identity.user_id = None
        await session.execute(delete(OAuthLoginAttempt).where(
            OAuthLoginAttempt.purpose == "mini_link", OAuthLoginAttempt.user_id == user.id,
        ))
        await session.commit()
    return {"ok": True}
