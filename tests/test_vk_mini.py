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
    assert set(ancestors) == {"'self'", "https://web.telegram.org", "https://*.telegram.org",
                              "https://vk.com", "https://m.vk.com", "https://vk.ru", "https://m.vk.ru"}
    assert 'data-vk="/static/vk-platform.js?v=' in response.text
    assert (await client.get("/static/vendor/vk-bridge.min.js")).status_code == 200


def native_payload(world, **changes):
    payload = {"full_name": "Новый Участник", "birth_date": "01.01.2000", "phone": "+79990001122",
               "region_id": world["moscow"].id, "university_name": "Университет VK Mini",
               "faculty": "Исторический", "course": 2, "education_level": "bachelor", "status": "activist"}
    payload.update(changes)
    return payload


async def test_native_manual_contact_requires_review_and_never_becomes_identity_proof(client, session, world, monkeypatch):
    monkeypatch.setattr("api.routers.register.IS_PRODUCTION", True)
    await start(client)
    context = (await client.get("/api/register/context", headers=headers())).json()
    assert context["manual_contact_allowed"] and not context["verified_phone"]
    response = await client.post("/api/register/submit", headers=headers(), json=native_payload(world))
    assert response.status_code == 200, response.text
    identity = (await session.execute(select(ExternalIdentity))).scalar_one()
    assert identity.verified_phone is None and identity.user_id is None
    assert (await client.get("/api/me", headers=headers())).status_code == 403
    application = (await session.execute(select(MembershipApplication))).scalar_one()
    user = await approve_application(session, application.id, world["leader_moscow"].id)
    assert user.role == "participant" and not user.is_superuser
    await session.refresh(identity)
    assert identity.user_id == user.id and identity.verified_phone is None
    assert (await client.get("/api/me", headers=headers())).json()["id"] == user.id


@pytest.mark.parametrize("signature", ["", "a" * 32, "forged", "a" * 64])
async def test_unsupported_or_forged_phone_never_silently_verifies(client, session, signature):
    await start(client)
    response = await client.post("/api/auth/vk-mini/phone", headers=headers(),
                                 json={"phone_number": "+79990001122", "sign": signature})
    assert response.status_code == 400, response.text
    assert not (await client.get("/api/register/phone", headers=headers())).json()["verified"]
    assert "+79990001122" not in response.text
    if signature:
        assert signature not in response.text


@pytest.mark.parametrize("birth", ["31.02.2000", "29.02.2001", "99.99.9999", "01.01.3000", "01.01.1899", "1.1.2000", "не дата"])
async def test_native_rejects_impossible_future_and_noncanonical_birth_dates(client, session, world, birth):
    await start(client)
    response = await client.post("/api/register/submit", headers=headers(), json=native_payload(world, birth_date=birth))
    assert response.status_code == 400, response.text
    assert not (await session.execute(select(MembershipApplication))).scalars().all()


async def test_manual_phone_cannot_enumerate_or_reserve_existing_accounts(client, session, world):
    session.add(TelegramPhoneVerification(telegram_id=world["federal"].telegram_id, phone="+79990001122"))
    await session.commit()
    await start(client)
    response = await client.post("/api/register/submit", headers=headers(), json=native_payload(world))
    assert response.status_code == 200, response.text
    assert (await client.get("/api/me", headers=headers())).status_code == 403
    assert (await client.get("/api/register/context", headers=headers())).json()["existing_account"] is False
    # A verified VK user can still submit their own number. An unverified
    # contact does not reserve it or expose whether a different cabinet exists.
    await start(client, uid="80002")
    response = await client.post("/api/register/submit", headers=headers("80002"), json=native_payload(world, phone="+79990003344"))
    assert response.status_code == 200, response.text
    await start(client, uid="80003")
    await client.post("/api/auth/vk-mini/phone", headers=headers("80003"), json=phone_proof("80003", "+79990003344"))
    response = await client.post("/api/register/submit", headers=headers("80003"), json=native_payload(world, phone="+79990003344"))
    assert response.status_code == 200, response.text


def test_birth_date_rules_apply_to_registration_profile_and_review():
    from datetime import date
    from pydantic import ValidationError
    from api.routers.profile import ProfilePatch
    from api.routers.applications import ApplicationPatch
    from utils.parser import parse_birth_date
    assert parse_birth_date("29.02.2000", date(2026, 10, 25)) == date(2000, 2, 29)
    for model in [ProfilePatch, ApplicationPatch]:
        for birth in ["3000-01-01", "1899-01-01", "2001-02-29"]:
            with pytest.raises(ValidationError):
                model(birth_date=birth)


async def test_invalid_cell_and_blank_names_never_create_applications(client, session, world):
    await start(client)
    for changes in [{"cell_id": 999999}, {"full_name": "   "}, {"faculty": "   "}]:
        response = await client.post("/api/register/submit", headers=headers(), json=native_payload(world, **changes))
        assert response.status_code in {400, 422}, response.text
    assert not (await session.execute(select(MembershipApplication))).scalars().all()
