from datetime import datetime, timedelta

from database.models import MemberQuestProgress, Quest
from tests.conftest import login
from tests.test_cabinet_revisions import student


async def me_count(client):
    return (await client.get('/api/me')).json()['counters']['personal_academy']


async def test_assignment_never_notifies_a_member_or_alumnus(client, session, world):
    member, owner = await student(session,world['moscow'].id,status='member')
    owner.is_superuser = True; owner.is_federal = True
    quest = Quest(title='Сыграть в килу',emoji='⚽',thresholds='1,3,10')
    session.add(quest); await session.flush()
    session.add(MemberQuestProgress(member_id=member.id,quest_id=quest.id,count=0,
        assigned_by_user_id=world['leader_moscow'].id,assigned_at=datetime(2026,10,4)))
    await session.commit(); login(owner)
    assert await me_count(client) == 0
    member.status='alumni'; await session.commit()
    assert await me_count(client) == 0
    member.status='activist'; await session.commit()
    assert await me_count(client) == 1


async def test_seen_reward_remains_claimable_and_new_reward_unread(client, session, world):
    member, owner = await student(session,world['moscow'].id,status='member')
    quest = Quest(title='Сыграть в килу',emoji='⚽',thresholds='1,3,10')
    session.add(quest); await session.flush()
    progress = MemberQuestProgress(member_id=member.id,quest_id=quest.id,count=1)
    session.add(progress); await session.commit(); login(owner)
    assert await me_count(client) == 1
    q = (await client.get('/api/character/me')).json()['quests'][0]
    assert q['is_unread'] and q['claimable_stars'] == 1
    assert (await client.post('/api/character/me/seen',json={'items':[{'quest_id':quest.id,'token':q['notice_token']}]})).status_code == 200
    assert await me_count(client) == 0
    assert (await client.get('/api/character/me')).json()['quests'][0]['claimable_stars'] == 1
    progress.count=3; await session.commit()
    assert await me_count(client) == 1
    # An old screen cannot acknowledge a reward earned after it was rendered.
    await client.post('/api/character/me/seen',json={'items':[{'quest_id':quest.id,'token':q['notice_token']}]})
    assert await me_count(client) == 1
    assert (await client.post(f'/api/character/me/quests/{quest.id}/claim')).status_code == 200
    assert await me_count(client) == 0


async def test_legacy_reward_and_changed_assignment_have_separate_new_notices(client, session, world):
    member, owner = await student(session,world['moscow'].id)
    quest = Quest(title='Сыграть в килу',emoji='⚽',thresholds='1,3,10')
    session.add(quest); await session.flush()
    progress = MemberQuestProgress(member_id=member.id,quest_id=quest.id,count=0,
        assigned_by_user_id=world['leader_moscow'].id,assigned_at=datetime(2026,10,4))
    session.add(progress); await session.commit(); login(owner)
    q=(await client.get('/api/character/me')).json()['quests'][0]
    await client.post('/api/character/me/seen',json={'items':[{'quest_id':quest.id,'token':q['notice_token']}]})
    assert await me_count(client) == 0
    progress.assigned_at += timedelta(days=1); progress.assignment_note='Новая редакция'; await session.commit()
    assert await me_count(client) == 1
    quest.is_active=False; progress.earned_stars_floor=4; await session.commit()
    data=(await client.get('/api/character/me')).json()
    assert data['legacy_quests'][0]['claimable_stars'] == 4
    assert await me_count(client) == 1
