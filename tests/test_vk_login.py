import base64
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from api.auth import get_current_user
from api.main import app
from api.rate_limit import _limiter
from config import BOT_TOKEN
from database.models import BrowserSession, ExternalIdentity, Member, MembershipApplication, OAuthLoginAttempt, TelegramPhoneVerification, User
from services import vk_login
from services.applications import approve_application
from services.browser_login import COOKIE_NAME


@pytest.fixture(autouse=True)
def vk_config(monkeypatch):
    monkeypatch.setattr(vk_login, 'VK_ID_CLIENT_ID', '123456')
    monkeypatch.setattr(vk_login, 'WEBAPP_URL', 'http://test')
    for key in list(_limiter._events):
        if key[0].startswith('vk-login'):
            _limiter._events.pop(key)


def tma(user, age=0):
    data = {'auth_date':str(int(time.time())-age), 'user':json.dumps({'id':user.telegram_id,'first_name':user.full_name})}
    check = '\n'.join(f'{k}={data[k]}' for k in sorted(data))
    key = hmac.new(b'WebAppData', BOT_TOKEN.encode(), hashlib.sha256).digest()
    data['hash'] = hmac.new(key, check.encode(), hashlib.sha256).hexdigest()
    return {'Authorization':'tma '+urlencode(data), 'Origin':'http://test'}


async def begin(client, path='/api/auth/vk/start', headers=None):
    r = await client.post(path, headers=headers or {'Origin':'http://test'})
    assert r.status_code == 200, r.text
    return parse_qs(urlsplit(r.json()['url']).query)['state'][0]


async def finish(client, state):
    return await client.get('/api/auth/vk/callback', params={'state':state,'code':'provider-code','device_id':'device'}, follow_redirects=False)


def provider(monkeypatch, sub='121212', phone='+79990001122'):
    calls = []
    async def exchange(attempt, code, device, state):
        calls.append((code,device))
        return {'subject':sub,'full_name':'Новый Участник','verified_phone':phone}
    monkeypatch.setattr(vk_login, 'exchange_profile', exchange)
    return calls


async def test_pkce_state_is_bound_to_browser_and_single_use(client, session, monkeypatch):
    calls = provider(monkeypatch)
    state = await begin(client)
    row = (await session.execute(select(OAuthLoginAttempt))).scalar_one()
    assert row.state_hash != state
    assert row.code_verifier and len(row.code_verifier) >= 43
    # A foreign state must not clear the valid browser attempt cookie.
    bad = await finish(client, 'x' * 43)
    assert bad.status_code == 303 and 'login_error' in bad.headers['location']
    assert 'set-cookie' not in bad.headers and not calls
    binding = client.cookies.get(vk_login.BINDING_COOKIE)
    client.cookies.delete(vk_login.BINDING_COOKIE)
    assert 'login_error' in (await finish(client,state)).headers['location']
    assert not calls
    client.cookies.set(vk_login.BINDING_COOKIE,binding)
    success = await finish(client,state)
    assert parse_qs(urlsplit(success.headers['location']).query) == {'web':['1'],'view':['register'],'provider':['vk']}
    assert len(calls) == 1
    assert 'HttpOnly' in success.headers.get_list('set-cookie')[0]
    assert 'login_error' in (await finish(client,state)).headers['location']
    assert len(calls) == 1
    assert not (await session.execute(select(OAuthLoginAttempt))).scalars().all()


async def test_expired_attempt_never_calls_provider(client, session, monkeypatch):
    calls = provider(monkeypatch)
    state = await begin(client)
    attempt = (await session.execute(select(OAuthLoginAttempt))).scalar_one()
    attempt.expires_at = 0
    await session.commit()
    assert 'login_error' in (await finish(client,state)).headers['location']
    assert not calls


async def test_start_requires_same_origin_and_hides_disabled_provider(client, monkeypatch):
    assert (await client.post('/api/auth/vk/start')).status_code == 403
    assert (await client.post('/api/auth/vk/start',headers={'Origin':'https://evil.test'})).status_code == 403
    monkeypatch.setattr(vk_login,'VK_ID_CLIENT_ID','')
    assert (await client.get('/api/auth/options')).json()['vk'] is False
    assert (await client.post('/api/auth/vk/start',headers={'Origin':'http://test'})).status_code == 503


async def test_new_vk_member_requires_application_approval_and_keeps_participant_role(client, session, world, monkeypatch):
    provider(monkeypatch)
    await finish(client, await begin(client))
    assert (await client.get('/api/me')).status_code == 401
    context = (await client.get('/api/register/context')).json()
    assert context['provider'] == 'vk' and context['verified_phone'] == '+79990001122'
    payload = {'full_name':'Новый Участник Братства','birth_date':'01.01.2000','phone':context['verified_phone'],
        'region_id':world['moscow'].id,'university_name':'Университет VK','faculty':'Исторический',
        'course':2,'education_level':'bachelor','status':'activist','role':'superuser','is_superuser':True}
    submitted = await client.post('/api/register/submit',json=payload,headers={'Origin':'http://test'})
    assert submitted.status_code == 200, submitted.text
    assert (await client.get('/api/register/context')).json()['application']['state'] == 'pending'
    assert (await client.get('/api/me')).status_code == 401
    application = (await session.execute(select(MembershipApplication))).scalar_one()
    assert application.telegram_id is None and application.external_identity_id is not None
    user = await approve_application(session, application.id, world['leader_moscow'].id)
    assert user.telegram_id is None and user.role == 'participant' and not user.is_superuser
    assert user.member_id is not None
    result = await client.get('/api/me')
    assert result.status_code == 200, result.text
    assert result.json()['full_name'] == payload['full_name']
    assert (await client.post('/api/news/articles',json={'official':True},headers={'Origin':'http://test'})).status_code == 403


async def test_vk_registration_cannot_forge_phone_or_reuse_existing_contact(client, session, world, monkeypatch):
    provider(monkeypatch)
    await finish(client, await begin(client))
    payload = {'full_name':'Новый Участник Братства','birth_date':'01.01.2000','phone':'+79990009999',
        'region_id':world['moscow'].id,'university_name':'Университет','faculty':'История',
        'course':2,'education_level':'bachelor','status':'activist'}
    assert (await client.post('/api/register/submit',json=payload,headers={'Origin':'http://test'})).status_code == 400
    world['federal'].phone = '+79990001122'
    session.add(TelegramPhoneVerification(telegram_id=world['federal'].telegram_id,phone='+79990001122'))
    await session.commit()
    assert (await client.get('/api/register/context')).json()['existing_account'] is True
    payload['phone'] = '+79990001122'
    assert (await client.post('/api/register/submit',json=payload,headers={'Origin':'http://test'})).status_code == 409
    assert not (await session.execute(select(MembershipApplication))).scalars().all()


async def test_editable_contact_cannot_reserve_another_vk_users_phone(client, session, world, monkeypatch):
    provider(monkeypatch)
    world['federal'].phone = '+79990001122'
    await session.commit()
    await finish(client, await begin(client))
    assert (await client.get('/api/register/context')).json()['existing_account'] is False


async def test_missing_provider_phone_does_not_allow_registration(client, world, monkeypatch):
    provider(monkeypatch,phone=None)
    await finish(client,await begin(client))
    assert (await client.get('/api/register/phone')).json() == {'verified':False,'phone':None}


async def test_explicit_link_preserves_existing_user_and_cannot_link_someone_elses_vk(client, session, world, monkeypatch):
    provider(monkeypatch)
    state = await begin(client,'/api/auth/vk/link',tma(world['federal']))
    response = await finish(client,state)
    assert parse_qs(urlsplit(response.headers['location']).query) == {'web':['1'],'vk':['connected']}
    identity = (await session.execute(select(ExternalIdentity))).scalar_one()
    assert identity.user_id == world['federal'].id
    assert (await client.get('/api/me')).json()['full_name'] == world['federal'].full_name
    state = await begin(client,'/api/auth/vk/link',tma(world['leader_tula']))
    assert 'login_error' in (await finish(client,state)).headers['location']
    await session.refresh(identity)
    assert identity.user_id == world['federal'].id


async def test_link_requires_recent_existing_proof_and_denies_impersonation(client, world):
    assert (await client.post('/api/auth/vk/link',headers=tma(world['federal'],age=400))).status_code == 403
    app.dependency_overrides[get_current_user] = lambda: world['federal']
    assert (await client.post('/api/auth/vk/link',headers={'Origin':'http://test'})).status_code == 403
    world['federal']._impersonated_by = world['superuser']
    assert (await client.post('/api/auth/vk/link',headers=tma(world['federal']))).status_code == 403


async def test_two_vk_only_browser_users_resolve_by_user_id_not_null_telegram(client, session, world):
    for index in range(2):
        member = Member(region_id=world['moscow'].id,full_name=f'Участник {index}')
        session.add(member); await session.flush()
        user = User(full_name=member.full_name,role='participant',member_id=member.id,telegram_id=None)
        session.add(user); await session.flush()
        identity = ExternalIdentity(provider='vk',subject=str(999000+index),full_name=user.full_name,user_id=user.id)
        session.add(identity); await session.flush()
        secret = ('a' if index == 0 else 'b') * 43
        session.add(BrowserSession(secret_hash=vk_login.digest(secret),grant_hash=None,code='',user_id=user.id,
            external_identity_id=identity.id,expires_at=int(time.time())+600))
        await session.commit()
        client.cookies.set(COOKIE_NAME,secret)
        result = await client.get('/api/me')
        assert result.status_code == 200, result.text
        assert result.json()['full_name'] == user.full_name
    user.is_active = False
    await session.commit()
    assert (await client.get('/api/me')).status_code == 401
    assert (await client.get('/api/auth/browser/status')).json() == {'authenticated':False,'registration':False}


async def test_pkce_challenge_and_callback_are_server_controlled(client, session):
    response = await client.post('/api/auth/vk/start',headers={'Origin':'http://test'},json={'redirect_uri':'https://evil.test/'})
    assert response.status_code == 422
    response = await client.post('/api/auth/vk/start',headers={'Origin':'http://test'},json={'article_id':42})
    params = parse_qs(urlsplit(response.json()['url']).query)
    row = (await session.execute(select(OAuthLoginAttempt))).scalar_one()
    expected = base64.urlsafe_b64encode(hashlib.sha256(row.code_verifier.encode()).digest()).decode().rstrip('=')
    assert params['code_challenge'] == [expected]
    assert params['redirect_uri'] == ['http://test/api/auth/vk/callback']
    assert params['client_id'] == ['123456'] and params['code_challenge_method'] == ['s256']
    assert row.return_article_id == 42


async def test_browser_shell_has_no_blocking_telegram_dependency(client):
    html = (await client.get('/')).text
    assert '<script src="https://telegram.org' not in html
    assert '/static/bootstrap.js?v=' in html
    assert 'data-app="/static/app.js?v=' in html and 'data-articles="/static/articles.js?v=' in html


async def test_logout_cancels_pending_vk_attempt(client, session, monkeypatch):
    calls = provider(monkeypatch)
    state = await begin(client)
    binding = client.cookies.get(vk_login.BINDING_COOKIE)
    response = await client.post('/api/auth/browser/logout',headers={'Origin':'http://test','Idempotency-Key':'vk-browser-logout-proof-test'})
    assert response.status_code == 200
    assert len(response.headers.get_list('set-cookie')) == 2
    client.cookies.set(vk_login.BINDING_COOKIE,binding)
    assert 'login_error' in (await finish(client,state)).headers['location']
    assert not calls and not (await session.execute(select(OAuthLoginAttempt))).scalars().all()


async def test_fragmented_provider_json_is_read_fully_and_bounded():
    class Content:
        async def iter_chunked(self, size):
            for value in [b'{"user":',b'{"user_id":',b'"123"}}']:
                yield value
    class Response:
        status = 200
        content = Content()
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
    class Client:
        def post(self,*args,**kwargs):
            assert args[0].startswith('https://id.vk.ru/')
            assert kwargs['allow_redirects'] is False
            return Response()
    assert await vk_login._provider_post(Client(),'/oauth2/user_info',{}) == {'user':{'user_id':'123'}}


async def test_provider_profile_must_match_token_subject(monkeypatch):
    replies = [{'access_token':'not-retained','state':'expected-state','user_id':100}, {'user':{'user_id':'200','first_name':'Wrong'}}]
    async def post(*args): return replies.pop(0)
    monkeypatch.setattr(vk_login,'_provider_post',post)
    attempt = OAuthLoginAttempt(code_verifier='v'*64)
    with pytest.raises(HTTPException):
        await vk_login.exchange_profile(attempt,'code','device','expected-state')


async def test_unlink_revokes_vk_sessions_but_preserves_telegram_access(client, session, world, monkeypatch):
    provider(monkeypatch)
    await finish(client, await begin(client, '/api/auth/vk/link', tma(world['federal'])))
    old_secret = client.cookies.get(COOKIE_NAME)
    identity = (await session.execute(select(ExternalIdentity))).scalar_one()
    session.add(BrowserSession(secret_hash=vk_login.digest('t'*43),grant_hash=None,code='',
        user_id=world['federal'].id,expires_at=int(time.time())+600))
    await session.commit()
    assert (await client.delete('/api/auth/vk/link', headers={'Origin':'http://test'})).status_code == 403
    assert (await client.delete('/api/auth/vk/link', headers=tma(world['federal']))).status_code == 200
    await session.refresh(identity)
    assert identity.user_id is None
    assert not (await session.execute(select(BrowserSession).where(BrowserSession.external_identity_id==identity.id))).scalars().all()
    client.cookies.set(COOKIE_NAME,old_secret)
    assert (await client.get('/api/me')).status_code == 401
    client.cookies.set(COOKIE_NAME,'t'*43)
    assert (await client.get('/api/me')).json()['full_name'] == world['federal'].full_name
    assert (await client.get('/api/me',headers=tma(world['federal']))).status_code == 200


async def test_callback_preserves_only_validated_article_and_rejects_duplicate_state(client, monkeypatch):
    calls = provider(monkeypatch)
    response = await client.post('/api/auth/vk/start',headers={'Origin':'http://test'},json={'article_id':42})
    state = parse_qs(urlsplit(response.json()['url']).query)['state'][0]
    duplicate = await client.get('/api/auth/vk/callback',params=[('state',state),('state',state),('code','a'),('device_id','b')],follow_redirects=False)
    assert 'login_error' in duplicate.headers['location'] and not calls
    response = await finish(client,state)
    assert parse_qs(urlsplit(response.headers['location']).query)['article'] == ['42']


async def test_provider_response_over_limit_is_rejected_before_parsing():
    class Content:
        async def iter_chunked(self, size):
            for _ in range(5):
                yield b' ' * (16*1024)
    class Response:
        status = 200
        content = Content()
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
    class Client:
        def post(self,*args,**kwargs): return Response()
    with pytest.raises(HTTPException) as error:
        await vk_login._provider_post(Client(),'/oauth2/user_info',{})
    assert error.value.status_code == 502


async def test_link_opens_existing_cabinet_in_a_pending_vk_browser(client, world, monkeypatch):
    provider(monkeypatch)
    await finish(client, await begin(client))
    pending_secret = client.cookies.get(COOKIE_NAME)
    client.cookies.clear()  # Link from a separately opened Telegram webview.
    await finish(client, await begin(client,'/api/auth/vk/link',tma(world['federal'])))
    client.cookies.set(COOKIE_NAME,pending_secret)
    response = await client.get('/api/me')
    assert response.status_code == 200, response.text
    assert response.json()['full_name'] == world['federal'].full_name
