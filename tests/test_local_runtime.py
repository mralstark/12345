import asyncio
import json
from unittest.mock import AsyncMock

from scripts.local_runtime import recovery_decision, url_from_line


def test_only_registration_events_set_a_valid_public_origin():
    event={'type':'v1.tcpip_forward.register.accepted','data':'https://example.lhr.life\n'+('QR'*20000)}
    assert url_from_line(json.dumps(event))=='https://example.lhr.life'
    for value in ['[]','null','bad json',json.dumps({'type':'request','data':'https://evil.lhr.life'}),
                  json.dumps({'type':event['type'],'data':'https://test.lhr.life.attacker.invalid'})]:
        assert url_from_line(value) is None
    assert url_from_line(json.dumps({**event,'data':'https://example.localhost.run'}))=='https://example.localhost.run'


def test_public_flaps_do_not_trigger_local_service_restarts():
    for failures in range(1,6):
        assert not recovery_decision('tunnel',failures,True)
    assert recovery_decision('tunnel',6,True)
    assert recovery_decision('api',3,True)
    assert recovery_decision('bot',0,False)


async def test_readiness_checks_database_and_caches_bounded_probes(client,monkeypatch):
    import api.readiness as r
    monkeypatch.setattr(r,'_lock',asyncio.Lock())
    monkeypatch.setattr(r,'_checked_at',float('-inf'))
    monkeypatch.setattr(r,'_ready',False)
    probe=AsyncMock(return_value=True);monkeypatch.setattr(r,'_probe',probe)
    responses=await asyncio.gather(*[client.get('/readyz') for _ in range(8)])
    assert all(response.status_code==200 for response in responses)
    assert probe.await_count==1
    monkeypatch.setattr(r,'_checked_at',float('-inf'))
    probe.side_effect=ConnectionError('private connection details must not be exposed')
    failed=await client.get('/readyz')
    assert failed.status_code==503 and failed.json()=={'status':'unavailable'}
    assert (await client.get('/healthz')).status_code==200


async def test_real_readiness_authenticates_database_canary(session):
    from api.readiness import _probe
    from database.encryption_state import write_state
    from sqlalchemy import text
    conn=await session.connection();await write_state(conn);await session.commit()
    assert await _probe()
    await session.execute(text("UPDATE data_encryption_state SET canary='invalid'"));await session.commit()
    try:
        assert not await _probe()
    except RuntimeError:
        pass # ready() translates authenticated corruption to a generic 503.
