"""VK Mini Apps authentication, independent of iframe/third-party cookies.

Mini App subjects are namespaced separately from VK ID OAuth subjects: provider
identifiers must never be assumed interchangeable across integrations.
"""
import base64
import hashlib
import hmac
import re
import secrets
import time
from urllib.parse import parse_qsl, urlencode

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from config import VK_MINI_APP_ID, VK_MINI_APP_SECRET
from database.db import async_session
from database.models import ExternalIdentity, OAuthLoginAttempt, User
from services.browser_login import digest
from utils.parser import normalize_phone

PROVIDER = "vk_mini"
MAX_AGE = 3600
LINK_TTL = 600


def enabled():
    return bool(VK_MINI_APP_ID and VK_MINI_APP_SECRET)


def validate_launch(raw: str, max_age: int = MAX_AGE) -> dict:
    if not enabled():
        raise HTTPException(503, "Подключение VK Mini App ещё не завершено")
    if not raw or len(raw.encode("utf-8")) > 8192:
        raise HTTPException(401, "Заново откройте приложение во ВКонтакте")
    try:
        items = parse_qsl(raw, keep_blank_values=True, max_num_fields=64, errors="strict")
        params = dict(items)
        if len(items) != len(params):
            raise ValueError
        signed = {k: params[k] for k in sorted(params) if k.startswith("vk_")}
        expected = base64.urlsafe_b64encode(hmac.new(
            VK_MINI_APP_SECRET.encode(), urlencode(signed).encode(), hashlib.sha256,
        ).digest()).decode().rstrip("=")
        if not hmac.compare_digest(expected, params.get("sign", "")):
            raise ValueError
        if params.get("vk_app_id") != VK_MINI_APP_ID:
            raise ValueError
        uid, issued = params["vk_user_id"], params["vk_ts"]
        if (not uid or len(uid) > 20 or not all(c in "0123456789" for c in uid) or int(uid) <= 0
                or not issued or len(issued) > 16 or not all(c in "0123456789" for c in issued)):
            raise ValueError
        timestamp = int(issued)
        now = time.time()
        if timestamp <= 0 or timestamp > now + 60 or now - timestamp > max_age:
            raise ValueError
        return {"subject": f"{VK_MINI_APP_ID}:{int(uid)}", "vk_user_id": str(int(uid))}
    except (ValueError, KeyError, UnicodeError, TypeError):
        raise HTTPException(401, "Данные входа устарели или недействительны. Переоткройте приложение в VK") from None


def launch_from_header(authorization: str) -> dict:
    scheme, _, raw = authorization.partition(" ")
    if scheme.lower() != "vkma" or not raw:
        raise HTTPException(401, "Откройте приложение из ВКонтакте")
    return validate_launch(raw)


async def find_identity(session, launch, *, lock=False):
    query = select(ExternalIdentity).where(
        ExternalIdentity.provider == PROVIDER, ExternalIdentity.subject == launch["subject"],
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return (await session.execute(query)).scalar_one_or_none()


async def require_identity(session, launch):
    identity = await find_identity(session, launch)
    if identity is None:
        raise HTTPException(401, "Заново откройте приложение во ВКонтакте")
    if identity.user_id is not None:
        user = await session.get(User, identity.user_id)
        if user is None or not user.is_active:
            raise HTTPException(403, "Доступ к кабинету закрыт. Обратитесь к руководителю отделения")
    return identity


async def start(launch, full_name="", link_token=None):
    async with async_session() as session:
        identity = await find_identity(session, launch, lock=True)
        if identity is None:
            identity = ExternalIdentity(provider=PROVIDER, subject=launch["subject"], full_name=full_name)
            session.add(identity)
        if link_token is not None:
            attempt = (await session.execute(delete(OAuthLoginAttempt).where(
                OAuthLoginAttempt.state_hash == digest(link_token),
                OAuthLoginAttempt.purpose == "mini_link",
                OAuthLoginAttempt.binding_hash == digest(VK_MINI_APP_ID),
                OAuthLoginAttempt.expires_at > int(time.time()),
            ).returning(OAuthLoginAttempt))).scalar_one_or_none()
            if attempt is None:
                raise HTTPException(409, "Ссылка подключения использована или устарела. Создайте новую в Telegram")
            user = await session.get(User, attempt.user_id)
            if user is None or not user.is_active or user.view_as_user_id is not None:
                raise HTTPException(403, "Подключение к этому кабинету недоступно")
            if identity.user_id is not None and identity.user_id != user.id:
                raise HTTPException(409, "Этот VK-аккаунт уже подключён к другому кабинету")
            identity.user_id = user.id
        if identity.user_id is not None:
            user = await session.get(User, identity.user_id)
            if user is None or not user.is_active:
                raise HTTPException(403, "Доступ к кабинету закрыт")
        # The display name is a form suggestion, never proof of identity/role.
        if full_name:
            identity.full_name = full_name
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise HTTPException(409, "Привязка уже изменена. Откройте кабинет заново") from None
        return {"registered": identity.user_id is not None}


async def create_link(user_id):
    token = secrets.token_urlsafe(32)
    async with async_session() as session:
        await session.execute(delete(OAuthLoginAttempt).where(
            OAuthLoginAttempt.purpose == "mini_link", OAuthLoginAttempt.user_id == user_id,
        ))
        session.add(OAuthLoginAttempt(state_hash=digest(token), binding_hash=digest(VK_MINI_APP_ID),
            code_verifier="", purpose="mini_link", user_id=user_id,
            expires_at=int(time.time()) + LINK_TTL))
        await session.commit()
    return {"url": f"https://vk.ru/app{VK_MINI_APP_ID}#link={token}", "expires_in": LINK_TTL}


def verify_phone(launch, phone_number, signature):
    if not re.fullmatch(r"[a-f0-9]{64}", signature):
        raise HTTPException(400, "VK не передал поддерживаемую подпись телефона. Укажите контактный номер вручную")
    # VK signs the original phone field. Normalize only after authenticating it.
    raw = f"{VK_MINI_APP_ID}{VK_MINI_APP_SECRET}{launch['vk_user_id']}phone_number{phone_number}"
    expected = hashlib.sha256(raw.encode()).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(400, "VK не подтвердил номер. Повторите передачу телефона")
    return normalize_phone(phone_number)
