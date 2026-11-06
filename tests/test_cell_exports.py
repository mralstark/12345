from io import BytesIO
import pytest
from openpyxl import load_workbook
from database.models import Member, Transaction, User
from tests.conftest import login

async def prepared(client, mode='all', region_id=None):
    return await client.post('/api/downloads/prepare', json={'kind':'cells','mode':mode,'region_id':region_id})

async def test_export_scoped_and_download_revoked(client, session, world):
    user=world['leader_moscow'];login(user)
    region=world['moscow'];cell=world['mgimo']
    session.add(Member(region_id=region.id,cell_id=cell.id,full_name='=HYPERLINK("https://example.com")'))
    await session.commit()
    response=await prepared(client, region_id=region.id)
    assert response.status_code==200
    data=await client.get(response.json()['url']);assert data.status_code==200
    wb=load_workbook(BytesIO(data.content))
    assert set(wb.sheetnames)=={'Ячейки','Состав','Финансы','Операции','О выгрузке'}
    names=[r[2] for r in wb['Состав'].iter_rows(min_row=2)]
    assert any(c.value.startswith('=HYPERLINK') and c.data_type=='s' for c in names)
    assert {r[0] for r in wb['Ячейки'].iter_rows(min_row=2,values_only=True)}=={region.name}
    region.leader_user_id=None;await session.commit()
    assert (await client.get(response.json()['url'])).status_code==403

@pytest.mark.parametrize('actor',['participant','cell_leader'])
async def test_no_bulk_export_for_ordinary_or_cell_roles(client,session,world,actor):
    if actor == 'participant':
        user = User(full_name='Обычный участник',role='participant',telegram_id=991111)
        session.add(user);await session.commit()
    else:
        user=world[actor]
    login(user);assert (await prepared(client)).status_code==403

async def test_coordinator_cannot_export_other_region(client,world):
    login(world['coordinator'])
    assert (await prepared(client,region_id=world['tula'].id)).status_code==403

async def test_finance_keeps_unassigned_regional_money_out(client,session,world):
    from datetime import date
    r=world['moscow'].id;c=world['mgimo'].id
    session.add_all([Transaction(region_id=r,cell_id=c,type='income',amount=12345,date=date.today()),Transaction(region_id=r,type='income',amount=999999,date=date.today())])
    await session.commit();login(world['leader_moscow'])
    response=await prepared(client,'finance',r)
    file=await client.get(response.json()['url'])
    wb=load_workbook(BytesIO(file.content))
    assert wb['Финансы']['D2'].value==123.45

async def test_status_validation_and_empty_forming_cell(client, session, world):
    login(world['leader_moscow']);cell=world['mgimo']
    assert (await client.patch(f'/api/cells/{cell.id}',json={'formation_status':'invalid'})).status_code==422
    r=await client.patch(f'/api/cells/{cell.id}',json={'formation_status':'forming'})
    assert r.status_code==200 and r.json()['formation_status']=='forming'
    listing=await client.get('/api/cells',params={'region_id':world['moscow'].id})
    assert any(c['id']==cell.id for c in listing.json()['items'])
    login(world['cell_leader'])
    assert (await client.patch(f'/api/cells/{cell.id}',json={'formation_status':'registered'})).status_code==403

@pytest.mark.parametrize('mode,expected',[('list',{'Ячейки','О выгрузке'}),('members',{'Ячейки','Состав','О выгрузке'}),('finance',{'Ячейки','Финансы','Операции','О выгрузке'})])
async def test_workbook_modes(client,world,mode,expected):
    login(world['leader_moscow']);r=await prepared(client,mode)
    assert r.status_code==200
    file=await client.get(r.json()['url']);assert file.status_code==200
    assert set(load_workbook(BytesIO(file.content)).sheetnames)==expected
