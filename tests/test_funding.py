"""Synthetic financial-ledger regressions: scope, immutable history and secrecy."""
import asyncio
import json
from datetime import date
from io import BytesIO
from uuid import uuid4

import pytest
from openpyxl import load_workbook
from sqlalchemy import func, select, text

from fastapi import HTTPException
from database.db import engine, async_session
from database.encryption_state import ensure_encryption_ready, schema_hash, write_state
from database.models import BureauMember, CoordinatorRegion, FundingEntry, FundingPlan, FundingPlanAudit, Transaction, User
from tests.conftest import login
from utils.data_crypto import DataEncryptionError

MONTH = '2026-10'
PRE_FUNDING_HASH = '7dac02060a32993c6d10dba40f55e8c5d0a143435975a0b383a0d7aeb242cafa'


@pytest.fixture(autouse=True)
def deterministic_funding_clock(monkeypatch):
    monkeypatch.setattr('services.funding.tz_today', lambda: date(2026, 11, 6))
    import api.rate_limit as rate_limit
    monkeypatch.setattr(rate_limit, '_limiter', rate_limit.SlidingWindowLimiter())


async def plan(client, region_id=None, *, amount=100000, month=MONTH, revision=0, note='План на согласованные мероприятия'):
    response = await client.put('/api/funding/plan', json={
        'region_id': region_id, 'month': month, 'amount': amount, 'revision': revision, 'note': note,
    })
    assert response.status_code == 200, response.text
    return response.json()


def issuance(plan_id, **overrides):
    result = {'plan_id': plan_id, 'amount': 12345, 'date': '2026-10-21',
              'comment': 'Поэтический вечер', 'request_id': str(uuid4())}
    result.update(overrides)
    return result


async def overview(client, *, region_id=None, month=MONTH):
    params = {'month': month}
    if region_id is not None:
        params['region_id'] = region_id
    response = await client.get('/api/funding', params=params)
    assert response.status_code == 200, response.text
    return response.json()


async def test_one_shared_plan_and_issued_sum_in_both_cabinets(client, session, world):
    rid = world['moscow'].id
    login(world['coordinator'])
    row = await plan(client, rid)
    issued = await client.post('/api/funding/entries', json=issuance(row['id']))
    assert issued.status_code == 200
    coordinator = await overview(client, region_id=rid)
    login(world['leader_moscow'])
    leader = await overview(client, region_id=rid)
    assert leader['items'] == [{**coordinator['items'][0], 'can_manage': False}]
    assert leader['items'][0]['issued'] == 12345
    assert leader['items'][0]['remaining'] == 87655
    assert (await session.execute(select(func.count(Transaction.id)))).scalar_one() == 0


async def test_coordinator_scope_and_independent_shared_department(client, world):
    login(world['federal'])
    await plan(client, world['moscow'].id, amount=100000)
    other = await plan(client, world['tula'].id, amount=999999)
    await plan(client, None, amount=50000)
    login(world['coordinator'])
    data = await overview(client)
    assert {r['region_id'] for r in data['items']} == {None, world['moscow'].id}
    assert data['totals']['planned'] == 150000
    assert all(r['can_manage'] for r in data['items'])
    forbidden = await client.get('/api/funding', params={'month': MONTH, 'region_id': world['tula'].id})
    assert forbidden.status_code == 403
    assert (await client.post('/api/funding/entries', json=issuance(other['id']))).status_code == 403
    assert (await client.get(f"/api/funding/plans/{other['id']}/history")).status_code == 403
    login(world['leader_moscow'])
    data = await overview(client)
    assert [r['region_id'] for r in data['items']] == [world['moscow'].id]
    department_edit = await client.put('/api/funding/plan', json={'month': MONTH, 'amount': 1000})
    assert department_edit.status_code == 403


async def test_leader_cannot_change_or_cancel_coordinator_records(client, world):
    login(world['coordinator']); row = await plan(client, world['moscow'].id)
    result = await client.post('/api/funding/entries', json=issuance(row['id']))
    entry_id = result.json()['id']
    login(world['leader_moscow'])
    assert (await client.put('/api/funding/plan', json={'month': MONTH, 'region_id': world['moscow'].id,
            'amount': 200000, 'revision': 1})).status_code == 403
    assert (await client.post('/api/funding/entries', json=issuance(row['id']))).status_code == 403
    assert (await client.post(f'/api/funding/entries/{entry_id}/cancel', json={
        'reason': 'Ошибка суммы', 'request_id': str(uuid4())})).status_code == 403


@pytest.mark.parametrize('actor', ['ordinary', 'cell_leader', 'inactive'])
async def test_unprivileged_accounts_cannot_read_or_write_funding(client, session, world, actor):
    if actor == 'cell_leader':
        user = world['cell_leader']
    else:
        user = User(full_name='Синтетический участник', role='participant', telegram_id=987654,
                    is_active=actor != 'inactive')
        session.add(user); await session.commit()
    login(user)
    assert (await client.get('/api/funding', params={'month': MONTH})).status_code == 403
    assert (await client.put('/api/funding/plan', json={'month': MONTH, 'amount': 1})).status_code == 403


async def test_combined_coordinator_and_superuser_flags_keep_full_scope(client, session, world):
    user = world['coordinator']; user.is_superuser = True
    await session.commit(); login(user)
    data = await overview(client)
    assert {r['region_id'] for r in data['items']} == {None, world['moscow'].id, world['tula'].id}
    await plan(client, world['tula'].id)


async def test_leader_and_coordinator_combination_only_manages_coordinated_regions(client, session, world):
    user = world['leader_tula']; user.is_coordinator = True
    await session.execute(text('UPDATE coordinator_regions SET coordinator_user_id=:uid'), {'uid': user.id})
    await session.commit(); login(user)
    data = await overview(client)
    by_region = {r['region_id']: r for r in data['items']}
    assert by_region[world['moscow'].id]['can_manage'] is True
    assert by_region[world['tula'].id]['can_manage'] is False
    await plan(client, world['moscow'].id)
    assert (await client.put('/api/funding/plan', json={'month': MONTH, 'amount': 5000,
        'region_id': world['tula'].id})).status_code == 403


async def test_unassigned_bureau_membership_does_not_grant_department_financial_access(client, session, world):
    user = world['leader_moscow']
    session.add(BureauMember(user_id=user.id, title='Синтетическая должность без регионов'))
    await session.commit(); login(user)
    data = await overview(client)
    assert [r['region_id'] for r in data['items']] == [world['moscow'].id]
    assert data['can_manage'] is False and data['items'][0]['can_manage'] is False
    assert (await client.put('/api/funding/plan', json={'month': MONTH, 'amount': 5000})).status_code == 403


@pytest.mark.parametrize('replay', [False, True])
async def test_concurrent_issuances_preserve_budget_and_durable_idempotency(client, session, world, replay):
    from services.funding import issue
    user = world['coordinator']; login(user)
    row = await plan(client, world['moscow'].id, amount=100000)
    request_id = str(uuid4())

    async def submit(key):
        async with async_session() as isolated:
            try:
                result = await issue(isolated, user, row['id'], 70000, date(2026, 10, 21),
                                     'Параллельная выдача', key)
                return 200, result
            except HTTPException as error:
                return error.status_code, None

    result = await asyncio.gather(submit(request_id), submit(request_id if replay else str(uuid4())))
    assert sorted(status for status, _ in result) == ([200, 200] if replay else [200, 409])
    if replay:
        assert len({body['id'] for _, body in result}) == 1
        assert sorted(body['replayed'] for _, body in result) == [False, True]
    assert (await session.execute(select(func.count(FundingEntry.id)))).scalar_one() == 1
    assert (await overview(client, region_id=world['moscow'].id))['items'][0]['issued'] == 70000


async def test_issuance_replay_and_mismatched_replay(client, session, world):
    login(world['coordinator']); row = await plan(client, world['moscow'].id)
    payload = issuance(row['id'])
    first = await client.post('/api/funding/entries', json=payload)
    second = await client.post('/api/funding/entries', json=payload)
    assert first.status_code == second.status_code == 200
    assert first.json()['id'] == second.json()['id']
    assert second.json()['replayed'] is True
    mismatch = await client.post('/api/funding/entries', json={**payload, 'amount': 12346})
    assert mismatch.status_code == 409
    assert (await session.execute(select(func.count(FundingEntry.id)))).scalar_one() == 1


async def test_cancellation_is_append_only_idempotent_and_cannot_repeat(client, session, world):
    login(world['coordinator']); row = await plan(client, world['moscow'].id)
    first = await client.post('/api/funding/entries', json=issuance(row['id']))
    entry_id = first.json()['id']
    payload = {'reason': 'Неверная сумма в исходной записи', 'request_id': str(uuid4())}
    cancelled = await client.post(f'/api/funding/entries/{entry_id}/cancel', json=payload)
    replay = await client.post(f'/api/funding/entries/{entry_id}/cancel', json=payload)
    assert cancelled.status_code == replay.status_code == 200
    assert cancelled.json()['id'] == replay.json()['id'] and replay.json()['replayed']
    assert (await client.post(f'/api/funding/entries/{entry_id}/cancel', json={**payload, 'request_id': str(uuid4())})).status_code == 409
    assert (await client.post(f"/api/funding/entries/{cancelled.json()['id']}/cancel", json={**payload, 'request_id': str(uuid4())})).status_code == 422
    data = await overview(client, region_id=world['moscow'].id)
    assert data['items'][0]['issued'] == 0 and data['items'][0]['remaining'] == 100000
    entries = (await client.get('/api/funding/entries', params={'month': MONTH})).json()['items']
    original = next(e for e in entries if e['id'] == entry_id)
    assert original['cancelled'] and not original['can_cancel']
    assert (await session.execute(select(func.count(FundingEntry.id)))).scalar_one() == 2


async def test_revision_conflict_and_audit_preserve_previous_plan(client, world):
    login(world['coordinator']); row = await plan(client, world['moscow'].id, amount=10000, note='Основание первого плана')
    updated = await plan(client, world['moscow'].id, amount=20000, revision=row['revision'], note='Уточнённое основание плана')
    assert updated['revision'] == 2
    conflict = await client.put('/api/funding/plan', json={'region_id': world['moscow'].id,
        'month': MONTH, 'amount': 30000, 'revision': 1, 'note': 'Устаревшая вкладка'})
    assert conflict.status_code == 409
    history = await client.get(f"/api/funding/plans/{row['id']}/history")
    assert history.status_code == 200
    assert [h['revision'] for h in history.json()['items']] == [2, 1]
    assert history.json()['items'][0]['before']['amount'] == 10000
    assert history.json()['items'][0]['after']['amount'] == 20000


async def test_plan_overrun_requires_explicit_acknowledgement(client, world):
    login(world['coordinator']); row = await plan(client, world['moscow'].id, amount=100)
    payload = issuance(row['id'], amount=200)
    assert (await client.post('/api/funding/entries', json=payload)).status_code == 409
    assert (await client.post('/api/funding/entries', json={**payload, 'allow_over_plan': True})).status_code == 200
    data = await overview(client, region_id=world['moscow'].id)
    assert data['items'][0]['remaining'] == -100


async def test_monthly_plans_zero_vs_missing_and_department_are_distinct(client, world):
    login(world['coordinator'])
    missing = await overview(client, region_id=world['moscow'].id)
    assert missing['items'][0]['plan'] is None and missing['items'][0]['remaining'] is None
    october = await plan(client, world['moscow'].id, amount=0)
    november = await plan(client, world['moscow'].id, amount=54321, month='2026-11')
    department = await plan(client, None, amount=1000)
    assert len({october['id'], november['id'], department['id']}) == 3
    assert (await overview(client, region_id=world['moscow'].id))['items'][0]['remaining'] == 0
    assert (await overview(client, month='2026-11', region_id=world['moscow'].id))['items'][0]['remaining'] == 54321


@pytest.mark.parametrize('month', ['2026-00', '2026-13', '26-10', '2026-10-01', '../2026-10', '2026-1'])
async def test_invalid_months_rejected(client, world, month):
    login(world['coordinator'])
    assert (await client.get('/api/funding', params={'month': month})).status_code == 422


@pytest.mark.parametrize('amount', [-1, 2_147_483_648, 1.5, True, '1000'])
async def test_plan_amount_is_bounded_integer_kopecks(client, world, amount):
    login(world['coordinator'])
    assert (await client.put('/api/funding/plan', json={'month': MONTH, 'region_id': world['moscow'].id,
            'amount': amount})).status_code == 422


@pytest.mark.parametrize('override', [
    {'amount': 0}, {'amount': -1}, {'amount': 1.5}, {'amount': True},
    {'amount': 2_147_483_648}, {'comment': '   '}, {'comment': 'ab'},
    {'date': '2026-10-40'}, {'date': '2026-09-21'}, {'request_id': 'not-a-uuid'},
])
async def test_invalid_issuance_never_creates_a_ledger_entry(client, session, world, override):
    login(world['coordinator']); row = await plan(client, world['moscow'].id)
    assert (await client.post('/api/funding/entries', json=issuance(row['id'], **override))).status_code == 422
    assert (await session.execute(select(func.count(FundingEntry.id)))).scalar_one() == 0


async def test_future_issue_and_cancel_reason_validation(client, session, world):
    login(world['coordinator']); row = await plan(client, world['moscow'].id, month='2026-11')
    future = await client.post('/api/funding/entries', json=issuance(row['id'], date='2026-11-07'))
    assert future.status_code == 422
    first = await client.post('/api/funding/entries', json=issuance(row['id'], date='2026-11-06'))
    bad = await client.post(f"/api/funding/entries/{first.json()['id']}/cancel", json={'request_id': str(uuid4()), 'reason': '     '})
    assert bad.status_code == 422


async def test_export_stores_formula_like_strings_as_text_and_rechecks_scope(client, session, world):
    user = world['coordinator']; login(user)
    row = await plan(client, world['moscow'].id, note='=HYPERLINK("https://example.invalid")')
    issued = await client.post('/api/funding/entries', json=issuance(row['id'], comment='=1+1'))
    assert issued.status_code == 200
    response = await client.post('/api/funding/export', params={'month': MONTH})
    assert response.status_code == 200
    file = await client.get(response.json()['url'])
    assert file.status_code == 200
    wb = load_workbook(BytesIO(file.content))
    assert {'Планы', 'Выдачи', 'Сводка', 'Условия'} == set(wb.sheetnames)
    formula_like = [cell for ws in wb for cells in ws for cell in cells if isinstance(cell.value, str) and cell.value.startswith('=')]
    assert formula_like and all(c.data_type == 's' and c.hyperlink is None for c in formula_like)
    assignment = (await session.execute(select(CoordinatorRegion).where(CoordinatorRegion.coordinator_user_id == user.id))).scalar_one()
    await session.delete(assignment); await session.commit()
    assert (await client.get(response.json()['url'])).status_code == 403


async def test_region_leader_export_does_not_include_department_or_other_region(client, world):
    login(world['federal']); await plan(client, None, amount=777)
    await plan(client, world['tula'].id, amount=888)
    await plan(client, world['moscow'].id, amount=999)
    login(world['leader_moscow'])
    response = await client.post('/api/funding/export', params={'month': MONTH})
    assert response.status_code == 200
    file = await client.get(response.json()['url'])
    assert file.status_code == 200
    wb = load_workbook(BytesIO(file.content))
    assert [r[0] for r in wb['Сводка'].iter_rows(min_row=2, values_only=True)] == ['Москва']


async def test_private_plan_notes_issue_comments_and_audit_are_encrypted(client, session, world):
    login(world['coordinator']); row = await plan(client, world['moscow'].id, note='Секретный синтетический план')
    assert (await client.post('/api/funding/entries', json=issuance(row['id'], comment='Закрытый комментарий выдачи'))).status_code == 200
    raw_note = (await session.execute(text('SELECT note FROM funding_plans'))).scalar_one()
    raw_comment = (await session.execute(text('SELECT comment FROM funding_entries'))).scalar_one()
    raw_audit = (await session.execute(text('SELECT payload FROM funding_plan_audits'))).scalar_one()
    assert all(value.startswith('bdb1:') for value in (raw_note, raw_comment, raw_audit))
    assert 'Секретный' not in raw_note and 'Закрытый' not in raw_comment and 'Секретный' not in raw_audit


async def test_migration_upgrades_only_known_authenticated_empty_funding_inventory(session, world, monkeypatch):
    import scripts.migrate_funding as migration
    monkeypatch.setattr(migration, 'engine', engine)
    async with engine.begin() as conn:
        await write_state(conn)
        await conn.execute(text('UPDATE data_encryption_state SET schema_hash=:hash'), {'hash': PRE_FUNDING_HASH})
        await conn.execute(text('DROP TABLE funding_plan_audits'))
        await conn.execute(text('DROP TABLE funding_entries'))
        await conn.execute(text('DROP TABLE funding_plans'))
    await migration.migrate()
    await migration.migrate()
    async with engine.begin() as conn:
        await ensure_encryption_ready(conn)
        current = (await conn.execute(text('SELECT schema_hash FROM data_encryption_state'))).scalar_one()
        assert current == schema_hash()
        assert (await conn.execute(text('SELECT count(*) FROM users'))).scalar_one() == 6


@pytest.mark.parametrize('state_hash', [PRE_FUNDING_HASH, 'f' * 64])
async def test_migration_rejects_unmarked_nonempty_funding_or_unknown_inventory(client, session, world, monkeypatch, state_hash):
    import scripts.migrate_funding as migration
    monkeypatch.setattr(migration, 'engine', engine)
    login(world['coordinator']); await plan(client, world['moscow'].id)
    async with engine.begin() as conn:
        await write_state(conn)
        await conn.execute(text('UPDATE data_encryption_state SET schema_hash=:hash'), {'hash': state_hash})
    with pytest.raises(DataEncryptionError):
        await migration.migrate()


@pytest.mark.parametrize('field,path,value',[
    ('note','/api/funding/plan','bad\x00note'),
    ('comment','/api/funding/entries','bad\x01comment'),
    ('reason','/api/funding/entries/1/cancel','bad\uffffreason'),
])
async def test_financial_text_rejects_xml_control_characters(client,world,field,path,value):
    login(world['coordinator'])
    if field=='note':
        response=await client.put(path,json={'month':MONTH,'region_id':world['moscow'].id,'amount':10,'note':value})
    elif field=='comment':
        response=await client.post(path,json=issuance(1,comment=value))
    else:
        response=await client.post(path,json={'reason':value,'request_id':str(uuid4())})
    assert response.status_code==422


async def test_export_cancellations_have_signed_amount_and_legacy_controls_are_safe(client,session,world):
    login(world['coordinator'])
    row=await plan(client,world['moscow'].id)
    issued=await client.post('/api/funding/entries',json=issuance(row['id']))
    assert (await client.post(f"/api/funding/entries/{issued.json()['id']}/cancel",json={'reason':'Исправление суммы','request_id':str(uuid4())})).status_code==200
    world['moscow'].name='Москва\x00'
    await session.commit()
    response=await client.post('/api/funding/export',params={'month':MONTH})
    file=await client.get(response.json()['url'])
    assert file.status_code==200
    wb=load_workbook(BytesIO(file.content))
    assert sum(r[2] for r in wb['Выдачи'].iter_rows(min_row=2,values_only=True))==0
    assert 'Москва\ufffd' in [r[0] for r in wb['Планы'].iter_rows(min_row=2,values_only=True)]


async def test_encrypted_offline_clone_keeps_cancel_reference(client,session,world,tmp_path):
    from scripts.data_encryption import migrate_database
    from sqlalchemy.ext.asyncio import create_async_engine
    login(world['coordinator']);p=await plan(client,world['moscow'].id)
    issued=await client.post('/api/funding/entries',json=issuance(p['id']))
    assert (await client.post(f"/api/funding/entries/{issued.json()['id']}/cancel",json={'reason':'Контроль переноса отмены','request_id':str(uuid4())})).status_code==200
    async with engine.begin() as conn:await write_state(conn)
    target_url=f"sqlite+aiosqlite:///{(tmp_path/'copy.db').as_posix()}"
    result=await migrate_database(str(engine.url),target_url)
    target=create_async_engine(target_url)
    try:
        async with target.connect() as conn:
            rows=(await conn.execute(select(FundingEntry.__table__).order_by(FundingEntry.id))).mappings().all()
            assert len(rows)==2 and rows[1]['cancels_id']==rows[0]['id']
            assert rows[1]['kind']=='cancel' and rows[1]['comment']=='Контроль переноса отмены'
            await ensure_encryption_ready(conn)
    finally:await target.dispose()
