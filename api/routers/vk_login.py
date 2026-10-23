"""Browser VK ID entry and explicit account linking; no role assignment."""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select
from pydantic import BaseModel, ConfigDict, Field
from urllib.parse import urlencode

from api.auth import InitDataError, get_current_user, validate_init_data
from api.rate_limit import _limiter
from config import BOT_TOKEN, IS_PRODUCTION
from database.db import async_session
from database.models import BrowserSession, ExternalIdentity, User
from services import vk_login
from services.browser_login import COOKIE_NAME, SESSION_TTL, same_origin

router = APIRouter(prefix='/auth', tags=['web authentication'])
logger = logging.getLogger(__name__)


class StartIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    article_id: int | None = Field(default=None, ge=1, le=2147483647)


def limit(request, scope, budget=5):
    ip = request.client.host if request.client else ''
    retry = _limiter.check(scope, ip, budget, 60) or _limiter.check(scope + '-global', 0, 60, 60)
    if retry:
        raise HTTPException(429, 'Подождите минуту перед повторным входом', headers={'Retry-After':str(retry)})


def bind_cookie(response, binding):
    response.set_cookie(vk_login.BINDING_COOKIE, binding, max_age=vk_login.ATTEMPT_TTL,
                        secure=IS_PRODUCTION, httponly=True, samesite='lax', path='/')


def require_fresh_telegram(request, user):
    # Connecting/removing a recovery method requires fresh proof of the
    # existing identity, not just possession of a potentially old web cookie.
    same_origin(request)
    scheme, _, raw = request.headers.get('authorization','').partition(' ')
    if getattr(user, '_impersonated_by', None) or user.telegram_id is None or scheme.lower() != 'tma':
        raise HTTPException(403, 'Для подключения VK заново откройте свой кабинет в Telegram')
    try:
        validate_init_data(raw, BOT_TOKEN, max_age=300)
    except InitDataError:
        raise HTTPException(403, 'Заново откройте кабинет в Telegram и повторите действие') from None


@router.get('/options')
async def options():
    from services import vk_mini
    result = {'telegram':bool(BOT_TOKEN), 'vk':vk_login.enabled()}
    if vk_mini.enabled():
        result['vk_mini'] = {'app_id': vk_mini.VK_MINI_APP_ID}
    return result


@router.post('/vk/start')
async def start(request: Request, response: Response, payload: StartIn | None = None):
    same_origin(request)
    limit(request, 'vk-login-start')
    url, binding = await vk_login.start_attempt(article_id=payload.article_id if payload else None)
    bind_cookie(response, binding)
    return {'url':url}


@router.post('/vk/link')
async def link(request: Request, response: Response, payload: StartIn | None = None, user: User = Depends(get_current_user)):
    require_fresh_telegram(request, user)
    limit(request, 'vk-login-link')
    url, binding = await vk_login.start_attempt(user.id, payload.article_id if payload else None)
    bind_cookie(response, binding)
    return {'url':url}


@router.get('/vk/callback')
async def callback(request: Request):
    # The one cross-origin GET that changes login state is bound to both an
    # expiring server attempt and its HttpOnly browser cookie, then consumed.
    query = request.query_params
    consumed = False
    try:
        limit(request, 'vk-login-callback', 20)
        for key in ('state','code','device_id','error'):
            if len(query.getlist(key)) > 1:
                raise HTTPException(400, 'Некорректный ответ VK ID')
        state = query.get('state','')
        attempt = await vk_login.consume_attempt(state, request.cookies.get(vk_login.BINDING_COOKIE,''))
        consumed = True
        if query.get('error'):
            raise HTTPException(400, 'Вход отменён')
        code, device = query.get('code',''), query.get('device_id','')
        if not (1 <= len(code) <= 2048 and 1 <= len(device) <= 256):
            raise HTTPException(400, 'VK ID не передал код входа')
        profile = await vk_login.exchange_profile(attempt, code, device, state)
        secret, registered = await vk_login.finish_login(attempt, profile, request.cookies.get(COOKIE_NAME,''))
        target = {'web':'1', 'vk':'connected'} if registered else {'web':'1', 'view':'register', 'provider':'vk'}
        if attempt.return_article_id:
            target['article'] = str(attempt.return_article_id)
        response = RedirectResponse('/?' + urlencode(target), status_code=303)
        response.set_cookie(COOKIE_NAME, secret, max_age=SESSION_TTL, secure=IS_PRODUCTION,
                            httponly=True, samesite='strict', path='/')
    except HTTPException as exc:
        kind = ('vk_unavailable' if exc.status_code >= 500 else 'vk_conflict' if exc.status_code == 409
                else 'vk_access_closed' if exc.status_code == 403 else 'vk_expired' if not consumed
                else 'vk_cancelled' if query.get('error') else 'vk_failed')
        response = RedirectResponse('/?web=1&login_error=' + kind, status_code=303)
    if consumed:
        response.delete_cookie(vk_login.BINDING_COOKIE, secure=IS_PRODUCTION, httponly=True, samesite='lax', path='/')
    return response


@router.get('/vk/account')
async def account(user: User = Depends(get_current_user)):
    async with async_session() as session:
        connected = (await session.execute(select(ExternalIdentity.id).where(
            ExternalIdentity.provider == 'vk', ExternalIdentity.user_id == user.id,
        ))).scalar_one_or_none() is not None
    return {'enabled':vk_login.enabled(), 'connected':connected, 'can_link':user.telegram_id is not None,
            'can_unlink':connected and user.telegram_id is not None}


@router.delete('/vk/link')
async def unlink(request: Request, user: User = Depends(get_current_user)):
    require_fresh_telegram(request, user)
    async with async_session() as session:
        identity = (await session.execute(select(ExternalIdentity).where(
            ExternalIdentity.provider == 'vk', ExternalIdentity.user_id == user.id,
        ).with_for_update())).scalar_one_or_none()
        if identity:
            await session.execute(delete(BrowserSession).where(BrowserSession.external_identity_id == identity.id))
            identity.user_id = None
            await session.commit()
    return {'ok':True}
