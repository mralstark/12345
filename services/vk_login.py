"""VK ID authorization-code + PKCE. No provider tokens enter browser/storage."""
import base64
import hashlib
import json
import secrets
import time
from urllib.parse import urlencode, urlsplit

import aiohttp
from fastapi import HTTPException, Request
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from config import IS_PRODUCTION, VK_ID_CLIENT_ID, WEBAPP_URL
from database.db import async_session
from database.models import BrowserSession, ExternalIdentity, OAuthLoginAttempt, User
from services.browser_login import COOKIE_NAME, SESSION_TTL, digest
from utils.parser import normalize_phone

VK_ORIGIN = 'https://id.vk.ru'
ATTEMPT_TTL = 600
BINDING_COOKIE = '__Host-bratstvo_vk_attempt' if IS_PRODUCTION else 'bratstvo_vk_attempt'


def enabled():
    return bool(VK_ID_CLIENT_ID and WEBAPP_URL)


def callback_url():
    url = urlsplit(WEBAPP_URL or '')
    return f'{url.scheme}://{url.netloc}/api/auth/vk/callback'


async def start_attempt(user_id=None, article_id=None):
    if not enabled():
        raise HTTPException(503, 'Вход с VK ID ещё не подключён')
    state, binding, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    now = int(time.time())
    async with async_session() as session:
        await session.execute(delete(OAuthLoginAttempt).where(OAuthLoginAttempt.expires_at <= now))
        session.add(OAuthLoginAttempt(state_hash=digest(state), binding_hash=digest(binding),
            code_verifier=verifier, purpose='link' if user_id is not None else 'login', user_id=user_id,
            return_article_id=article_id,
            expires_at=now + ATTEMPT_TTL))
        await session.commit()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    params = {'response_type':'code', 'client_id':VK_ID_CLIENT_ID, 'redirect_uri':callback_url(),
              'code_challenge':challenge, 'code_challenge_method':'s256', 'state':state, 'scope':'phone'}
    return VK_ORIGIN + '/authorize?' + urlencode(params), binding


async def consume_attempt(state, binding):
    if len(state) != 43 or len(binding) != 43:
        raise HTTPException(400, 'Ссылка входа устарела. Начните вход заново')
    async with async_session() as session:
        row = (await session.execute(delete(OAuthLoginAttempt).where(
            OAuthLoginAttempt.state_hash == digest(state), OAuthLoginAttempt.binding_hash == digest(binding),
            OAuthLoginAttempt.expires_at > int(time.time()),
        ).returning(OAuthLoginAttempt))).scalar_one_or_none()
        await session.commit()
        if row is None:
            raise HTTPException(400, 'Ссылка входа устарела или уже использована')
        return row


async def _provider_post(client, endpoint, fields):
    async with client.post(VK_ORIGIN + endpoint, data=fields, allow_redirects=False) as response:
        if response.status != 200:
            raise HTTPException(502, 'VK ID временно не отвечает. Повторите вход позже')
        raw = bytearray()
        async for chunk in response.content.iter_chunked(16 * 1024):
            raw.extend(chunk)
            if len(raw) > 64 * 1024:
                raise HTTPException(502, 'Некорректный ответ VK ID')
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeError):
            raise HTTPException(502, 'Некорректный ответ VK ID') from None
        if not isinstance(data, dict) or data.get('error'):
            raise HTTPException(400, 'VK ID не подтвердил вход. Попробуйте снова')
        return data


def subject(value):
    value = str(value)
    if not (1 <= len(value) <= 20) or not all(c in '0123456789' for c in value) or int(value) <= 0:
        raise HTTPException(502, 'VK ID не передал идентификатор пользователя')
    return str(int(value))


async def exchange_profile(attempt, code, device_id, state):
    try:
        timeout = aiohttp.ClientTimeout(total=15, connect=5)
        async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as client:
            token = await _provider_post(client, '/oauth2/auth', {
                'grant_type':'authorization_code', 'client_id':VK_ID_CLIENT_ID, 'redirect_uri':callback_url(),
                'code_verifier':attempt.code_verifier, 'code':code, 'device_id':device_id, 'state':state,
            })
            if token.get('state') != state or not isinstance(token.get('access_token'), str) or not token['access_token']:
                raise HTTPException(400, 'VK ID не подтвердил запрос входа')
            token_subject = subject(token.get('user_id'))
            profile = await _provider_post(client, '/oauth2/user_info', {'client_id':VK_ID_CLIENT_ID, 'access_token':token['access_token']})
    except (aiohttp.ClientError, TimeoutError):
        # Do not interpolate provider responses, URLs, tokens or authorization codes.
        raise HTTPException(503, 'Нет связи с VK ID. Повторите вход позже') from None
    data = profile.get('user')
    if not isinstance(data, dict) or subject(data.get('user_id')) != token_subject:
        raise HTTPException(400, 'VK ID не подтвердил пользователя')
    name = ' '.join(' '.join(str(data.get(key) or '') for key in ('first_name','last_name')).split())[:128]
    phone = None
    if data.get('phone'):
        try:
            phone = normalize_phone(str(data['phone']))
        except ValueError:
            pass  # A masked/withheld number must never count as verified.
    return {'subject':token_subject, 'full_name':name, 'verified_phone':phone}


async def finish_login(attempt, profile, previous_secret=''):
    secret = secrets.token_urlsafe(32)
    async with async_session() as session:
        identity = (await session.execute(select(ExternalIdentity).where(
            ExternalIdentity.provider == 'vk', ExternalIdentity.subject == profile['subject'],
        ).with_for_update())).scalar_one_or_none()
        if identity is None:
            identity = ExternalIdentity(provider='vk', subject=profile['subject'], full_name=profile['full_name'])
            session.add(identity)
        if attempt.purpose == 'link':
            user = await session.get(User, attempt.user_id)
            if user is None or not user.is_active or user.view_as_user_id is not None:
                raise HTTPException(403, 'Подключение VK недоступно для этого кабинета')
            if identity.user_id is not None and identity.user_id != user.id:
                raise HTTPException(409, 'Этот VK ID уже подключён к другому кабинету')
            identity.user_id = user.id
        identity.full_name = profile['full_name']
        identity.verified_phone = profile['verified_phone']
        if identity.user_id is not None:
            user = await session.get(User, identity.user_id)
            if user is None or not user.is_active:
                raise HTTPException(403, 'Доступ к кабинету закрыт')
        try:
            await session.flush()
            if attempt.purpose == 'link':
                # A browser may already be waiting with this same verified VK
                # identity. Explicit linking must open the existing cabinet
                # there too, rather than looping between registration/login.
                await session.execute(update(BrowserSession).where(
                    BrowserSession.external_identity_id == identity.id,
                    BrowserSession.user_id.is_(None),
                ).values(user_id=identity.user_id))
            if previous_secret:
                await session.execute(delete(BrowserSession).where(BrowserSession.secret_hash == digest(previous_secret)))
            session.add(BrowserSession(secret_hash=digest(secret), grant_hash=None, code='',
                user_id=identity.user_id, external_identity_id=identity.id, expires_at=int(time.time()) + SESSION_TTL))
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise HTTPException(409, 'У кабинета уже есть подключённый VK ID. Проверьте выбранный аккаунт') from None
        return secret, identity.user_id is not None


async def registration_identity(request: Request):
    secret = request.cookies.get(COOKIE_NAME, '')
    if len(secret) != 43:
        return None
    async with async_session() as session:
        identity = (await session.execute(select(ExternalIdentity).join(BrowserSession,
            BrowserSession.external_identity_id == ExternalIdentity.id).where(
                BrowserSession.secret_hash == digest(secret), BrowserSession.expires_at > int(time.time()),
                ExternalIdentity.provider == 'vk',
            ))).scalar_one_or_none()
        if identity is not None and identity.user_id is not None:
            user = await session.get(User, identity.user_id)
            if user is None or not user.is_active:
                return None
        if identity:
            session.expunge(identity)
        return identity
