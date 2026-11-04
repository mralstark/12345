import json

import pytest
from sqlalchemy import select, text

from database.models import Member, MentorPreparation, User
from database.encryption_state import ensure_encryption_ready, previous_schema_hash, schema_hash, write_state
from services.mentor_preparation import initial
from tests.conftest import login
from utils.data_crypto import DataEncryptionError


async def student(session, world, tid=81001, *, region="moscow"):
    member = Member(region_id=world[region].id, full_name="Тест подготовки", status="activist")
    session.add(member); await session.flush()
    user = User(telegram_id=tid, full_name=member.full_name, role="participant", member_id=member.id)
    session.add(user); await session.commit()
    return member, user


async def invited(client, session, world):
    member, user = await student(session, world)
    login(world["leader_moscow"])
    response = await client.post('/api/academy/preparation/invite', json={"member_id":member.id,"note":"Обсудим цели подготовки на встрече"})
    assert response.status_code == 200, response.text
    return member, user, response.json()


async def change(client, item, action, *, step=None, note=""):
    return await client.post('/api/academy/preparation/'+str(item['id']),json={"revision":item['revision'],"action":action,"step":step,"text":note})


async def test_full_path_requires_consent_review_and_manual_recommendation(client, session, world):
    member, user, item = await invited(client,session,world)
    login(user)
    assert (await change(client,item,'submit',step=0,note="Содержательный ответ")).status_code==409
    item=(await change(client,item,'accept')).json()
    assert (await change(client,item,'submit',step=1,note="Пропуск первого шага")).status_code==409
    for step in range(4):
        item=(await change(client,item,'draft',step=step,note="Мой черновик ответа")).json()
        item=(await change(client,item,'submit',step=step,note="Мой результат и выводы по этому шагу")).json()
        assert item['completed']==step
        login(world['leader_moscow'])
        assert (await change(client,item,'approve',step=step)).status_code==422
        if step==0:
            item=(await change(client,item,'changes',step=step,note="Добавьте конкретную цель")).json()
            login(user)
            item=(await change(client,item,'submit',step=step,note="Уточнённая цель и ожидаемый результат")).json()
            login(world['leader_moscow'])
        item=(await change(client,item,'approve',step=step,note="Результат понятен, шаг принят")).json()
        login(user)
    assert item['status']=='ready' and item['completed']==4
    assert (await change(client,item,'recommend',note="Рекомендую себя")).status_code==409
    login(world['leader_moscow'])
    item=(await change(client,item,'waitlist',note="Ожидаем свободного наставника")).json()
    assert item['status']=='waitlist'
    item=(await change(client,item,'recommend',note="Рекомендован после личного обсуждения")).json()
    assert item['status']=='recommended'
    await session.refresh(member)
    assert member.status=='activist' and member.stars==0
    raw=await session.scalar(text('SELECT payload FROM mentor_preparations'))
    assert raw.startswith('bdb1:') and 'Уточнённая' not in raw and 'Ожидаем' not in raw


async def test_scoped_access_duplicate_invitation_and_stale_revisions(client,session,world):
    member,user,item=await invited(client,session,world)
    login(world['leader_moscow'])
    again=await client.post('/api/academy/preparation/invite',json={"member_id":member.id,"note":"Другое приглашение"})
    assert again.json()['id']==item['id'] and again.json()['revision']==1
    login(world['leader_tula'])
    assert (await client.get('/api/academy/preparation/'+str(item['id']))).status_code==403
    assert (await change(client,item,'approve',step=0,note="Недопустимая проверка")).status_code==403
    assert (await client.get('/api/academy/preparation/participants')).json()['items']==[]
    login(user)
    assert (await client.get('/api/academy/preparation/participants')).status_code==403
    assert (await change(client,item,'accept')).status_code==200
    assert (await change(client,item,'accept')).status_code==409
    assert len((await session.execute(select(MentorPreparation))).scalars().all())==1


async def test_pause_withdraw_reinvite_preserves_work_and_membership_change_locks_writes(client,session,world):
    member,user,item=await invited(client,session,world)
    login(user)
    item=(await change(client,item,'accept')).json()
    item=(await change(client,item,'draft',step=0,note="Моя сохранённая цель")).json()
    item=(await change(client,item,'pause')).json()
    assert (await change(client,item,'submit',step=0,note="Не отправится")).status_code==409
    item=(await change(client,item,'resume')).json()
    item=(await change(client,item,'withdraw')).json()
    login(world['coordinator'])
    item=(await change(client,item,'reinvite',note="Новое обсуждение по вашему желанию")).json()
    login(user)
    item=(await change(client,item,'accept')).json()
    assert item['steps'][0]['response']=="Моя сохранённая цель"
    member.status='member';await session.commit()
    assert (await change(client,item,'pause')).status_code==409
    assert (await client.get('/api/academy/preparation/me')).json()['item']['eligible'] is False


async def test_candidates_only_registered_corporants_and_no_self_review(client,session,world):
    member,user,item=await invited(client,session,world)
    login(world['leader_moscow'])
    assert (await client.get('/api/academy/preparation/candidates?region_id='+str(world['tula'].id))).status_code==403
    user.member_id=None;await session.flush()
    world['leader_moscow'].member_id=member.id;await session.commit()
    login(world['leader_moscow'])
    item=(await change(client,item,'accept')).json()
    item=(await change(client,item,'submit',step=0,note="Самостоятельный ответ")).json()
    assert (await change(client,item,'approve',step=0,note="Сам себе проверяющий")).status_code==409


async def test_no_invitation_for_ordinary_participants_or_non_corporants(client,session,world):
    member,user=await student(session,world)
    other,second=await student(session,world,81002)
    login(user)
    assert (await client.post('/api/academy/preparation/invite',json={"member_id":other.id,"note":"Приглашаю другого"})).status_code==403
    login(world['leader_moscow']);other.status='member';await session.commit()
    assert (await client.post('/api/academy/preparation/invite',json={"member_id":other.id,"note":"Приглашаю другого"})).status_code==409
    login(second)
    assert (await client.get('/api/academy/preparation/me')).json()['item'] is None


async def test_additive_encryption_upgrade_is_authenticated_and_empty_only(session,world):
    conn=await session.connection()
    await write_state(conn)
    await conn.execute(text('UPDATE data_encryption_state SET schema_hash=:h'),{'h':previous_schema_hash()})
    await ensure_encryption_ready(conn)
    assert await conn.scalar(text('SELECT schema_hash FROM data_encryption_state'))==schema_hash()
    await conn.execute(text('UPDATE data_encryption_state SET schema_hash=:h'),{'h':previous_schema_hash()})
    await conn.execute(text("INSERT INTO mentor_preparations(member_id,invited_by_user_id,payload,revision) VALUES (999,1,'plaintext',1)"))
    with pytest.raises(DataEncryptionError):await ensure_encryption_ready(conn)
