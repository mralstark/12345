import asyncio
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest

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
    from utils.data_crypto import DataEncryptionError
    with pytest.raises(DataEncryptionError):
        await _probe()


def test_supervisor_keeps_bot_during_public_flaps_and_recovers_only_dead_api(tmp_path,monkeypatch):
    import scripts.local_runtime as local
    monkeypatch.setitem(sys.modules,'msvcrt',SimpleNamespace(LK_NBLCK=1,locking=lambda *a:None))
    class Process:
        dead=False
        def poll(self):return 0 if self.dead else None
    class Fake(local.Supervisor):
        tick=0
        def __init__(self):
            super().__init__({'state_dir':str(tmp_path),'code_dir':str(tmp_path),'runtime_dir':str(tmp_path)})
            self.starts={'api':0,'bot':0,'tunnel':0}
            self.running_snapshots=[]
        def save(self):pass
        def record(self,code):pass
        def stopping(self):return self.tick>=9
        def postgres(self):pass
        def configure(self,url):self.state['public_url']=url;self.env={'fixture':'1'}
        def stop(self,name):self.children.pop(name,None)
        def start(self,name):
            self.starts[name]+=1;self.children[name]=Process();self.launch_times[name]=1000+self.tick*10
        def tunnel(self):self.start('tunnel');self.urls.put('https://test.lhr.life')
        def api(self):self.start('api')
        def bot(self):self.start('bot')
        def bot_ready(self):return True
        def pause(self,seconds):
            self.running_snapshots.append(dict(self.starts));self.tick+=1
            if self.tick==7:self.children['api'].dead=True
    f=Fake()
    monkeypatch.setattr(local.time,'monotonic',lambda:f.tick*10)
    monkeypatch.setattr(local.time,'time',lambda:1000+f.tick*10)
    monkeypatch.setattr(local,'healthy',lambda url,**kw: True if url.startswith('http:') else f.tick not in {1,2,3})
    f.run()
    assert f.starts=={'api':2,'bot':1,'tunnel':1}
    assert all(s['bot']==1 for s in f.running_snapshots)
