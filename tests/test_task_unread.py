from datetime import timedelta

from database.models import Task
from tests.conftest import login
from utils.tz import now, today


async def test_overdue_tasks_seen_without_completing_and_new_task_stays_unread(client, session, world):
    author, owner = world['federal'], world['leader_moscow']
    old = Task(from_user_id=author.id, to_user_id=owner.id, title='Просрочено', deadline=today()-timedelta(days=5))
    session.add(old)
    await session.commit()
    login(owner)
    listed = (await client.get('/api/tasks')).json()['items']
    assert listed[0]['is_unread']
    new = Task(from_user_id=author.id, to_user_id=owner.id, title='Добавлена после просмотра')
    session.add(new)
    await session.commit()
    assert (await client.post('/api/tasks/seen', json={'items': [{'id': str(old.id)}]})).status_code == 200
    listed = {t['id']: t for t in (await client.get('/api/tasks')).json()['items']}
    assert not listed[old.id]['is_unread'] and listed[old.id]['effective_status'] == 'overdue'
    assert listed[new.id]['is_unread']
    assert (await client.get('/api/me')).json()['counters']['new_tasks'] == 1
    await client.post('/api/tasks/seen', json={'items': [{'id': str(new.id)}]})
    assert (await client.get('/api/me')).json()['counters']['new_tasks'] == 0


async def test_review_view_is_separate_and_repeat_submission_not_lost(client, session, world):
    author, owner = world['federal'], world['leader_moscow']
    task = Task(from_user_id=author.id, to_user_id=owner.id, title='На проверке', status='review', submitted_at=now().replace(tzinfo=None))
    session.add(task)
    await session.commit()
    login(author)
    listed = (await client.get('/api/tasks?box=outbox')).json()['items'][0]
    assert listed['is_unread'] and (await client.get('/api/me')).json()['counters']['management_tasks'] == 1
    body = {'items': [{'id': str(task.id), 'submitted_at': listed['submitted_at']}]}
    await client.post('/api/tasks/seen', json=body)
    assert (await client.get('/api/me')).json()['counters']['management_tasks'] == 0
    assert (await client.get('/api/tasks?box=outbox')).json()['items'][0]['status'] == 'review'
    await client.post(f'/api/tasks/{task.id}/status', json={'status': 'in_progress'})
    login(owner)
    await client.post(f'/api/tasks/{task.id}/status', json={'status': 'review'})
    login(author)
    await client.post('/api/tasks/seen', json=body)
    assert (await client.get('/api/me')).json()['counters']['management_tasks'] == 1
    await client.post(f'/api/tasks/{task.id}/read')
    assert (await client.get('/api/me')).json()['counters']['management_tasks'] == 0


async def test_legacy_review_can_be_seen_and_foreign_task_cannot(client, session, world):
    task = Task(from_user_id=world['federal'].id, to_user_id=world['leader_moscow'].id, title='Старый тест', status='review')
    session.add(task)
    await session.commit()
    login(world['leader_tula'])
    assert (await client.post('/api/tasks/seen', json={'items':[{'id':str(task.id)}]})).status_code == 403
    assert (await client.post(f'/api/tasks/{task.id}/read')).status_code == 403
    login(world['federal'])
    assert (await client.post('/api/tasks/seen', json={'items':[{'id':str(task.id)}]})).status_code == 200
    assert (await client.get('/api/me')).json()['counters']['management_tasks'] == 0


async def test_ball_is_in_honor_branch():
    from api.routers.character import _quest_dict
    from database.models import Quest
    quest = Quest(id=1, title='Принять участие в балу / танцевальном вечере', emoji='💃', thresholds='1', rewards='')
    assert _quest_dict(quest, 0)['branch_id'] == 'honor'
