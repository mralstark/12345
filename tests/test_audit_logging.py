import logging

from tests.test_vk_login import tma


async def test_mutation_audit_excludes_tokens_query_body_and_names(client, world, caplog):
    user = world["leader_moscow"]
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    response = await client.post("/api/me/seen?secret=synthetic-query-secret",
        headers=tma(user),
        json={"section": "news", "cursor": 0})
    assert response.status_code == 200
    audit = [record.message for record in caplog.records if record.message.startswith("security-audit ")]
    assert len(audit) == 1
    assert '"actor_id": ' + str(user.id) in audit[0]
    assert '"route": "/api/me/seen"' in audit[0]
    assert "synthetic-query-secret" not in audit[0]
    assert user.full_name not in audit[0]
    assert "Authorization" not in audit[0] and "auth_date" not in audit[0]


async def test_unauthenticated_requests_do_not_generate_actor_audit(client, caplog):
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    assert (await client.post("/api/me/seen", json={"section": "news", "cursor": 0})).status_code == 401
    assert not [r for r in caplog.records if r.message.startswith("security-audit ")]
