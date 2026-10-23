"""Real signature/auth boundaries with synthetic VK users; no provider traffic."""
import base64
import hashlib
import hmac
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from api.rate_limit import _limiter
from database.models import ExternalIdentity, MembershipApplication, OAuthLoginAttempt, TelegramPhoneVerification, User
from services import vk_mini
from services.applications import approve_application
from tests.test_vk_login import tma


@pytest.fixture(autouse=True)
def mini_config(monkeypatch):
    monkeypatch.setattr(vk_mini, "VK_MINI_APP_ID", "54848740")
    monkeypatch.setattr(vk_mini, "VK_MINI_APP_SECRET", "synthetic-mini-app-key-for-tests")
    _limiter._events.clear()


def launch(uid="80001", **changes):
    data = {"vk_app_id": vk_mini.VK_MINI_APP_ID, "vk_user_id": uid, "vk_ts": str(int(time.time())),
            "vk_platform": "desktop_web", "vk_language": "ru", "vk_access_token_settings": ""}
    data.update(changes)
    raw = urlencode({k: data[k] for k in sorted(data) if k.startswith("vk_")})
    data["sign"] = base64.urlsafe_b64encode(hmac.new(vk_mini.VK_MINI_APP_SECRET.encode(), raw.encode(), hashlib.sha256).digest()).decode().rstrip("=")
    return urlencode(data)


def headers(uid="80001", **changes):
    return {"Authorization": "vkma " + launch(uid, **changes)}


def phone_proof(uid="80001", phone="+79990001122"):
    raw = f"{vk_mini.VK_MINI_APP_ID}{vk_mini.VK_MINI_APP_SECRET}{uid}phone_number{phone}"
    return {"phone_number": phone, "sign": hashlib.sha256(raw.encode()).hexdigest()}


async def start(client, uid="80001", **payload):
    response = await client.post("/api/auth/vk-mini/start", headers=headers(uid), json=payload)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("changes", [
    {"vk_app_id": "111"}, {"vk_ts": "0"}, {"vk_ts": str(int(time.time()) - 7200)},
    {"vk_ts": str(int(time.time()) + 3600)}, {"vk_user_id": "0"}, {"vk_user_id": "-4"},
    {"vk_user_id": "1e3"}, {"vk_ts": "invalid"},
])
def test_rejects_signed_but_wrong_or_expired_launch(changes):
    with pytest.raises(HTTPException) as exc:
        vk_mini.validate_launch(launch(**changes))
    assert exc.value.status_code == 401


@pytest.mark.parametrize("raw", ["", "x" * 9000, "vk_user_id=3&sign=forged"])
def test_rejects_malformed_launch(raw):
    with pytest.raises(HTTPException):
        vk_mini.validate_launch(raw)


def test_checks_duplicates_and_all_signed_values():
    valid = launch()
    assert vk_mini.validate_launch(valid)["subject"] == "54848740:80001"
    for raw in [valid + "&vk_user_id=99", valid + "&sign=x", valid.replace("desktop_web", "android")]:
        with pytest.raises(HTTPException):
            vk_mini.validate_launch(raw)


async def test_unknown_native_user_gets_registration_without_cookies(client, session, world):
    assert await start(client, full_name="Новый Участник") == {"registered": False}
    assert not client.cookies
    response = await client.get("/api/register/context", headers=headers())
    assert response.status_code == 200, response.text
    assert response.json()["provider"] == "vk_mini"
    assert response.json()["full_name"] == "Новый Участник"
    assert response.json()["verified_phone"] is None
    assert (await client.get("/api/me", headers=headers())).status_code == 403
    assert (await client.get("/api/register/context")).status_code == 401
    assert len(list((await session.execute(select(ExternalIdentity))).scalars())) == 1
    await start(client)
    assert len(list((await session.execute(select(ExternalIdentity))).scalars())) == 1


async def test_phone_is_bound_to_vk_user_and_signed_original_value(client, session, world):
    await start(client)
    for proof in [phone_proof("80002"), dict(phone_proof(), phone_number="+79990009999")]:
        response = await client.post("/api/auth/vk-mini/phone", headers=headers(), json=proof)
        assert response.status_code == 400, response.text
    response = await client.post("/api/auth/vk-mini/phone", headers=headers(), json=phone_proof())
    assert response.status_code == 200, response.text
    context = (await client.get("/api/register/context", headers=headers())).json()
    assert context["verified_phone"] == "+79990001122"
    assert (await client.get("/api/register/phone", headers=headers())).json()["verified"]


async def test_new_native_account_requires_approval_and_never_inherits_admin_role(client, session, world):
    await start(client, full_name=world["federal"].full_name)
    await client.post("/api/auth/vk-mini/phone", headers=headers(), json=phone_proof())
    payload = {"full_name": "Новый Участник Братства", "birth_date": "01.01.2000", "phone": "+79990001122",
               "region_id": world["moscow"].id, "university_name": "Университет VK Mini", "faculty": "Исторический",
               "course": 2, "education_level": "bachelor", "status": "activist", "role": "superuser", "is_superuser": True}
    response = await client.post("/api/register/submit", headers=headers(), json=payload)
    assert response.status_code == 200, response.text
    assert (await client.get("/api/me", headers=headers())).status_code == 403
    application = (await session.execute(select(MembershipApplication))).scalar_one()
    user = await approve_application(session, application.id, world["leader_moscow"].id)
    assert user.role == "participant" and not user.is_superuser and user.telegram_id is None
    response = await client.get("/api/me", headers=headers())
    assert response.status_code == 200, response.text
    assert response.json()["id"] == user.id
    assert (await client.post("/api/finance/categories", headers=headers(), json={"region_id": world["moscow"].id,
                  "name": "Denied", "type": "expense"})).status_code == 403
    user.is_active = False
    await session.commit()
    assert (await client.get("/api/me", headers=headers())).status_code == 403
    assert (await client.get("/api/register/context", headers=headers())).status_code == 403


async def test_explicit_one_time_link_keeps_existing_user_and_revokes_immediately(client, session, world):
    actor = world["federal"]
    made = await client.post("/api/auth/vk-mini/link", headers=tma(actor))
    assert made.status_code == 200, made.text
    assert urlsplit(made.json()["url"]).netloc == "vk.ru"
    token = parse_qs(urlsplit(made.json()["url"]).fragment)["link"][0]
    attempt = (await session.execute(select(OAuthLoginAttempt))).scalar_one()
    assert attempt.state_hash != token and attempt.purpose == "mini_link"
    preview = await client.post("/api/auth/vk-mini/link-preview", headers=headers(), json={"link_token": token})
    assert preview.status_code == 200 and preview.json() == {"full_name": actor.full_name}
    assert (await client.get("/api/me", headers=headers())).status_code == 401
    assert await start(client, link_token=token) == {"registered": True}
    assert (await client.post("/api/auth/vk-mini/link-preview", headers=headers(), json={"link_token": token})).status_code == 409
    response = await client.get("/api/me", headers=headers())
    assert response.status_code == 200 and response.json()["id"] == actor.id
    assert (await client.post("/api/auth/vk-mini/start", headers=headers("80002"), json={"link_token": token})).status_code == 409
    assert (await client.delete("/api/auth/vk-mini/link", headers=headers())).status_code == 403
    assert (await client.delete("/api/auth/vk-mini/link", headers=tma(actor))).status_code == 200
    assert (await client.get("/api/me", headers=headers())).status_code == 403
    assert (await client.get("/api/me", headers=tma(actor))).status_code == 200


async def test_cannot_replace_foreign_link_or_claim_user_by_id(client, session, world):
    made = await client.post("/api/auth/vk-mini/link", headers=tma(world["leader_moscow"]))
    token = parse_qs(urlsplit(made.json()["url"]).fragment)["link"][0]
    await start(client, link_token=token)
    made = await client.post("/api/auth/vk-mini/link", headers=tma(world["federal"]))
    token = parse_qs(urlsplit(made.json()["url"]).fragment)["link"][0]
    assert (await client.post("/api/auth/vk-mini/start", headers=headers(), json={"link_token": token})).status_code == 409
    assert (await client.post("/api/auth/vk-mini/start", headers=headers(), json={"user_id": world["federal"].id})).status_code == 422
    assert (await client.get("/api/me", headers=headers())).json()["id"] == world["leader_moscow"].id


async def test_link_requires_fresh_telegram_proof_and_expires(client, session, world):
    assert (await client.post("/api/auth/vk-mini/link", headers=tma(world["federal"], age=400))).status_code == 403
    assert (await client.post("/api/auth/vk-mini/link", headers=headers())).status_code == 401
    made = await client.post("/api/auth/vk-mini/link", headers=tma(world["federal"]))
    token = parse_qs(urlsplit(made.json()["url"]).fragment)["link"][0]
    attempt = (await session.execute(select(OAuthLoginAttempt))).scalar_one()
    attempt.expires_at = int(time.time()) - 1
    await session.commit()
    assert (await client.post("/api/auth/vk-mini/link-preview", headers=headers(), json={"link_token": token})).status_code == 409
    assert (await client.post("/api/auth/vk-mini/start", headers=headers(), json={"link_token": token})).status_code == 409


async def test_vk_oauth_and_native_subjects_cannot_be_confused(client, session, world):
    session.add(ExternalIdentity(provider="vk", subject="80001", user_id=world["superuser"].id))
    await session.commit()
    assert await start(client) == {"registered": False}
    assert (await client.get("/api/me", headers=headers())).status_code == 403


async def test_matching_phone_does_not_automatically_link_accounts(client, session, world):
    session.add(TelegramPhoneVerification(telegram_id=world["federal"].telegram_id, phone="+79990001122"))
    await session.commit()
    await start(client)
    await client.post("/api/auth/vk-mini/phone", headers=headers(), json=phone_proof())
    context = (await client.get("/api/register/context", headers=headers())).json()
    assert context["existing_account"] is True
    assert (await client.get("/api/me", headers=headers())).status_code == 403


async def test_feature_off_fails_closed_and_never_exposes_secret(client, monkeypatch):
    options = (await client.get("/api/auth/options")).json()
    assert options["vk_mini"] == {"app_id": "54848740"}
    assert vk_mini.VK_MINI_APP_SECRET not in str(options)
    monkeypatch.setattr(vk_mini, "VK_MINI_APP_SECRET", "")
    assert (await client.post("/api/auth/vk-mini/start", headers=headers(), json={})).status_code == 503


async def test_vk_framing_is_explicit_and_bridge_is_served_locally(client):
    response = await client.get("/")
    ancestors = next(directive.split()[1:] for directive in response.headers["content-security-policy"].split(";")
                     if directive.strip().startswith("frame-ancestors "))
    assert "https://vk.ru" in ancestors
    assert "*" not in ancestors
    assert 'data-vk="/static/vk-platform.js?v=' in response.text
    assert (await client.get("/static/vendor/vk-bridge.min.js")).status_code == 200
