"""Existing Telegram cabinets link to a signed VK identity, never a contact."""
import time

import pytest
from sqlalchemy import func, select

from api.auth import TelegramIdentity
from api.routers.register import registered_user
from database.models import (
    APPLICATION_STATE_REJECTED, ExternalIdentity, Member, MembershipApplication,
    OAuthLoginAttempt, User,
)
from services import vk_mini
from services.applications import approve_application
from services.browser_login import digest
from tests.test_vk_login import tma
from tests.test_vk_mini import headers, launch, mini_config, native_payload, start


async def make_pair(client, uid="80001"):
    await start(client, uid=uid)
    response = await client.post("/api/auth/vk-mini/pair", headers=headers(uid))
    assert response.status_code == 200, response.text
    assert response.json()["expires_in"] == 600
    return response.json()["code"]


async def confirm(client, actor, code):
    return await client.post("/api/auth/vk-mini/pair-confirm", headers=tma(actor), json={"code": code})


async def test_pairing_keeps_cabinet_roles_and_does_not_create_member_or_user(client, session, world):
    actor = world["federal"]
    before = [(await session.execute(select(func.count()).select_from(model))).scalar_one() for model in (User, Member)]
    code = await make_pair(client)
    row = (await session.execute(select(OAuthLoginAttempt))).scalar_one()
    assert row.purpose == "mini_pair" and row.state_hash == digest(code) and code not in row.code_verifier
    assert row.binding_hash == digest(vk_mini.PROVIDER + ":54848740:80001")
    preview = await client.post("/api/auth/vk-mini/pair-preview", headers=tma(actor), json={"code": code})
    assert preview.json() == {"vk_user_id": "80001"}
    assert (await client.get("/api/me", headers=headers())).status_code == 403
    formatted = "-".join(code[i:i + 4].lower() for i in range(0, 12, 4))
    response = await confirm(client, actor, formatted)
    assert response.status_code == 200, response.text
    for auth in [tma(actor), headers()]:
        me = (await client.get("/api/me", headers=auth)).json()
        assert me["id"] == actor.id and me["role"] == actor.role
    after = [(await session.execute(select(func.count()).select_from(model))).scalar_one() for model in (User, Member)]
    assert after == before
    assert (await client.get("/api/register/context", headers=headers())).json() == {"already_registered": True}
    assert (await confirm(client, world["leader_tula"], code)).status_code == 409
    assert (await client.post("/api/auth/vk-mini/pair", headers=headers())).status_code == 409
    assert (await client.post("/api/auth/vk-mini/start", headers=headers("80002"), json={})).json() == {"registered": False}


async def test_pairing_rejects_stale_proof_missing_origin_forged_launch_and_payload_claims(client, session, world):
    code = await make_pair(client)
    actor = world["federal"]
    for auth in [headers(), tma(actor, age=400), {"Authorization": tma(actor)["Authorization"]}]:
        assert (await client.post("/api/auth/vk-mini/pair-confirm", headers=auth, json={"code": code})).status_code == 403
        assert (await client.post("/api/auth/vk-mini/pair-preview", headers=auth, json={"code": code})).status_code == 403
    assert (await client.post("/api/auth/vk-mini/pair")).status_code == 401
    assert (await client.post("/api/auth/vk-mini/pair", headers={"Authorization":"vkma " + launch().replace("80001", "80002")})).status_code == 401
    assert (await client.post("/api/auth/vk-mini/pair-confirm", headers=tma(actor), json={"code": code, "user_id": world["superuser"].id})).status_code == 422
    assert (await client.post("/api/auth/vk-mini/pair-confirm", headers=tma(actor), json={"code": "0" * 12})).status_code == 422
    assert (await confirm(client, actor, code)).status_code == 200


async def test_new_code_revokes_old_and_expiry_cannot_be_extended_by_preview(client, session, world):
    old = await make_pair(client)
    code = await make_pair(client)
    assert old != code
    assert (await confirm(client, world["federal"], old)).status_code == 409
    row = (await session.execute(select(OAuthLoginAttempt))).scalar_one()
    row.expires_at = int(time.time()) - 1
    await session.commit()
    assert (await client.post("/api/auth/vk-mini/pair-preview", headers=tma(world["federal"]), json={"code": code})).status_code == 409
    assert (await confirm(client, world["federal"], code)).status_code == 409


async def test_one_vk_per_cabinet_and_conflicts_do_not_consume_the_other_code(client, session, world):
    first = await make_pair(client)
    second = await make_pair(client, "80002")
    assert (await confirm(client, world["federal"], first)).status_code == 200
    assert (await confirm(client, world["federal"], second)).status_code == 409
    assert (await confirm(client, world["leader_tula"], second)).status_code == 200
    assert (await client.get("/api/me", headers=headers())).json()["id"] == world["federal"].id
    assert (await client.get("/api/me", headers=headers("80002"))).json()["id"] == world["leader_tula"].id


async def test_linking_closes_own_pending_duplicate_application_and_blocks_late_registration(client, session, world):
    await start(client)
    response = await client.post("/api/register/submit", headers=headers(), json=native_payload(world))
    assert response.status_code == 200, response.text
    application = (await session.execute(select(MembershipApplication))).scalar_one()
    code = await make_pair(client)
    assert (await confirm(client, world["federal"], code)).status_code == 200
    await session.refresh(application)
    assert application.state == APPLICATION_STATE_REJECTED
    assert "существующий кабинет" in application.reject_reason
    assert (await client.post("/api/register/submit", headers=headers(), json=native_payload(world))).status_code == 409
    # A stale dependency snapshot must not miss a newly linked cabinet.
    identity = (await session.execute(select(ExternalIdentity))).scalar_one()
    stale = TelegramIdentity(None, "VK", external_identity_id=identity.id, provider="vk_mini")
    assert (await registered_user(session, stale)).id == world["federal"].id
    with pytest.raises(ValueError, match="уже рассмотрена"):
        await approve_application(session, application.id, world["leader_moscow"].id)


async def test_unlink_revokes_vk_but_preserves_telegram_and_inactive_or_impersonated_cannot_link(client, session, world):
    code = await make_pair(client)
    actor = world["superuser"]
    actor.view_as_user_id = world["federal"].id
    await session.commit()
    assert (await confirm(client, actor, code)).status_code == 403
    actor.view_as_user_id = None
    actor.is_active = False
    await session.commit()
    assert (await confirm(client, actor, code)).status_code == 403
    actor.is_active = True
    await session.commit()
    assert (await confirm(client, actor, code)).status_code == 200
    assert (await client.delete("/api/auth/vk-mini/link", headers=tma(actor))).status_code == 200
    assert (await client.get("/api/me", headers=headers())).status_code == 403
    assert (await client.get("/api/me", headers=tma(actor))).status_code == 200


async def test_disabled_feature_rejects_pending_pairing(client, session, world, monkeypatch):
    code = await make_pair(client)
    monkeypatch.setattr(vk_mini, "VK_MINI_APP_SECRET", "")
    assert (await confirm(client, world["federal"], code)).status_code == 503
