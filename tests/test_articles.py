import io
import json

import pytest
from PIL import Image
from sqlalchemy import select

from database.models import Member, NewsPhoto, NewsPost, User
from services.articles import validate_content
from tests.conftest import login


async def draft(client, user):
    login(user)
    response = await client.post('/api/news/articles', json={'official': True})
    assert response.status_code == 200, response.text
    return response.json()


async def save(client, article, **extra):
    payload = {'title': 'История студенческих братств', 'summary': 'От первых объединений к сегодняшнему дню',
               'content': [{'tag': 'h3', 'children': ['Первые встречи']}, {'tag': 'p', 'children': ['Текст статьи ', {'tag': 'b', 'children': ['с выделением']}, '.']}],
               'revision': article['revision'], 'official': True}
    payload.update(extra)
    response = await client.put(f"/api/news/articles/{article['id']}", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


async def publish(client, article):
    response = await client.post(f"/api/news/articles/{article['id']}/publish", json={'revision': article['revision']})
    assert response.status_code == 200, response.text
    return response.json()


async def test_drafts_private_everywhere_and_publish_has_fresh_feed_id(client, session, world, monkeypatch):
    sent = []
    async def send(post_id):
        sent.append(post_id)
    monkeypatch.setattr('api.routers.news._send_news_notifications', send)
    a = await save(client, await draft(client, world['federal']))
    assert sent == []
    other = NewsPost(author_user_id=world['federal'].id, text='Свежее объявление')
    session.add(other)
    await session.commit()
    login(world['leader_tula'])
    assert (await client.get(f"/api/news/articles/{a['id']}")).status_code == 403
    feed = (await client.get('/api/news')).json()
    assert [i['id'] for i in feed['items']] == [other.id]
    assert (await client.get('/api/news/articles/drafts')).json()['items'] == []
    profile = (await client.get(f"/api/profile/{world['federal'].id}")).json()
    assert a['id'] not in [i['id'] for i in profile.get('posts', [])]
    assert (await client.post('/api/me/seen', json={'section':'news', 'cursor':other.id})).status_code == 200
    login(world['federal'])
    published = await publish(client, a)
    assert published['id'] > other.id and published['state'] == 'published'
    assert published['title'] == a['title'] and published['content'] == a['content']
    assert sent == [published['id']]
    assert (await client.get(f"/api/news/articles/{a['id']}")).status_code == 404
    login(world['leader_tula'])
    assert (await client.get('/api/me')).json()['counters']['news'] == 1
    item = (await client.get('/api/news')).json()['items'][0]
    assert item['content_kind'] == 'article' and item['article_title'] == a['title']
    assert (await client.get(f"/api/news/articles/{published['id']}")).status_code == 200


async def test_revisions_and_pending_changes_do_not_modify_published_article(client, world, monkeypatch):
    sent = []
    async def send(post_id): sent.append(post_id)
    monkeypatch.setattr('api.routers.news._send_news_notifications', send)
    a = await draft(client, world['federal'])
    saved = await save(client, a)
    assert (await client.put(f"/api/news/articles/{a['id']}", json={'revision':a['revision'], 'title':'Старая версия'})).status_code == 409
    pub = await publish(client, saved)
    edit = (await client.post(f"/api/news/articles/{pub['id']}/edit")).json()
    edit = await save(client, edit, title='Обновлённый заголовок')
    assert (await client.get(f"/api/news/articles/{pub['id']}")).json()['title'] == saved['title']
    again = (await client.post(f"/api/news/articles/{pub['id']}/edit")).json()
    assert again['id'] == edit['id']
    revised = await publish(client, edit)
    assert revised['id'] == pub['id'] and revised['title'] == 'Обновлённый заголовок'
    assert sent == [pub['id']]  # editing never re-sends a publication announcement


async def test_article_permissions_and_explicit_preview_publish(client, session, world):
    a = await draft(client, world['federal'])
    assert (await client.post(f"/api/news/articles/{a['id']}/publish", json={'revision':a['revision']})).status_code == 400
    login(world['superuser'])
    assert (await client.put(f"/api/news/articles/{a['id']}", json={'revision':a['revision']})).status_code == 403
    pub = None
    login(world['federal'])
    pub = await publish(client, await save(client, a))
    login(world['superuser'])
    assert (await client.post(f"/api/news/articles/{pub['id']}/edit")).status_code == 403
    member = Member(region_id=world['moscow'].id, full_name='Участник')
    session.add(member); await session.flush()
    user = User(full_name=member.full_name, role='participant', member_id=member.id, telegram_id=77881)
    session.add(user); await session.commit()
    login(user)
    assert (await client.post('/api/news/articles', json={'official':True})).status_code == 403
    assert (await client.get('/api/news/articles/drafts')).status_code == 403
    assert (await client.get(f"/api/news/articles/{pub['id']}")).status_code == 200


@pytest.mark.parametrize('content', [
    [{'tag':'script','children':['alert(1)']}],
    [{'tag':[], 'children':['text']}],
    [{'tag':'p','attrs':{'onclick':'bad()'},'children':['text']}],
    [{'tag':'a','attrs':{'href':'javascript:alert(1)'},'children':['click']}],
    [{'tag':'a','attrs':{'href':'https://good.test\\@evil.test'},'children':['click']}],
    [{'tag':'img','attrs':{'src':'https://external.test/track.png'}}],
    [{'tag':'img','attrs':{'photo_id':99}}],
    [{'tag':'iframe','attrs':{'src':'https://example.test'}}],
    [{'tag':'p','children':['x' * 60001]}],
])
def test_article_rejects_unsafe_or_unbounded_document(content):
    with pytest.raises(ValueError):
        validate_content(content, set())


async def test_inline_photo_ownership_survives_working_copy_and_deletion(client, session, world):
    a = await save(client, await draft(client, world['federal']))
    image = io.BytesIO(); Image.new('RGB',(200,120),(100,90,80)).save(image,format='PNG')
    response = await client.post(f"/api/news/articles/{a['id']}/photos", data={'revision':a['revision']}, files={'file':('image.png',image.getvalue(),'image/png')})
    assert response.status_code == 200, response.text
    a = response.json(); photo = a['photos'][0]
    content = [{'tag':'p','children':['Подпись и текст']}, {'tag':'figure','children':[
        {'tag':'img','attrs':{'photo_id':photo['id']}}, {'tag':'figcaption','children':['Встреча клуба']},
    ]}]
    a = await save(client, a, content=content)
    pub = await publish(client,a)
    feed = (await client.get('/api/news')).json()['items'][0]
    assert feed['cover_photo_id'] == photo['id']
    assert (await client.get(photo['url'])).status_code == 200
    edit = (await client.post(f"/api/news/articles/{pub['id']}/edit")).json()
    assert edit['photos'][0]['id'] == photo['id']
    edit = await save(client,edit,title='Изменённый текст',content=content)
    assert (await client.delete(f"/api/news/{edit['id']}")).status_code == 200
    assert (await client.get(photo['url'])).status_code == 200
    edit = (await client.post(f"/api/news/articles/{pub['id']}/edit")).json()
    assert (await client.delete(f"/api/news/{pub['id']}")).status_code == 200
    assert (await client.get(f"/api/news/articles/{edit['id']}")).status_code == 404
    assert (await client.get(photo['url'])).status_code == 404


async def test_migration_preserves_old_posts_and_is_repeatable(tmp_path, monkeypatch):
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text, inspect
    from scripts import migrate_articles
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path/'old.db').as_posix()}")
    try:
        async with engine.begin() as conn:
            await conn.execute(text('CREATE TABLE news_posts (id INTEGER PRIMARY KEY, text TEXT)'))
            await conn.execute(text('CREATE TABLE member_quest_progress (id INTEGER PRIMARY KEY)'))
            await conn.execute(text("INSERT INTO news_posts (text) VALUES ('Прежняя новость')"))
        monkeypatch.setattr(migrate_articles,'engine',engine)
        await migrate_articles.migrate(); await migrate_articles.migrate()
        async with engine.begin() as conn:
            row = (await conn.execute(text('SELECT text, publication_state, content_kind, article_revision FROM news_posts'))).one()
            assert tuple(row) == ('Прежняя новость','published','post',1)
    finally:
        await engine.dispose()


async def test_edit_conflict_preserves_both_published_and_working_text(client, session, world):
    pub = await publish(client, await save(client, await draft(client, world['federal'])))
    edit = (await client.post(f"/api/news/articles/{pub['id']}/edit")).json()
    edit = await save(client, edit, title='Мой новый текст')
    source = await session.get(NewsPost, pub['id'])
    source.article_revision += 1
    source.article_title = 'Уже опубликованная новая версия'
    await session.commit()
    result = await client.post(f"/api/news/articles/{edit['id']}/publish", json={'revision':edit['revision']})
    assert result.status_code == 409
    assert (await client.get(f"/api/news/articles/{pub['id']}")).json()['title'] == 'Уже опубликованная новая версия'
    assert (await client.get(f"/api/news/articles/{edit['id']}")).json()['title'] == 'Мой новый текст'


async def test_publish_retry_with_same_key_sends_only_once(client, world, monkeypatch):
    sent=[]
    async def send(post_id): sent.append(post_id)
    monkeypatch.setattr('api.routers.news._send_news_notifications',send)
    a=await save(client,await draft(client,world['federal']))
    import uuid
    headers={'Idempotency-Key':str(uuid.uuid4())}
    path=f"/api/news/articles/{a['id']}/publish"
    first=await client.post(path,json={'revision':a['revision']},headers=headers)
    retry=await client.post(path,json={'revision':a['revision']},headers=headers)
    assert first.status_code==retry.status_code==200
    assert first.json()['id']==retry.json()['id']
    assert sent==[first.json()['id']]


def test_deep_documents_rejected_without_recursion_failure():
    node='text'
    for _ in range(100): node={'tag':'p','children':[node]}
    with pytest.raises(ValueError,match='форматирование'):
        validate_content([node],set())


async def test_rollback_cannot_expose_saved_drafts(tmp_path, monkeypatch):
    import tarfile
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text
    from scripts import check_rollback_compatibility as check
    path=tmp_path/'old-code.tar.gz'
    with tarfile.open(path,'w:gz') as archive:
        for name in ['services/news.py','services/navigation.py','api/routers/profile.py']:
            item=tarfile.TarInfo(name); source=b'# Old code without draft filtering'; item.size=len(source)
            archive.addfile(item,io.BytesIO(source))
    engine=create_async_engine(f"sqlite+aiosqlite:///{(tmp_path/'rollback.db').as_posix()}")
    try:
        monkeypatch.setattr(check,'engine',engine)
        async with engine.begin() as conn:
            await conn.execute(text('CREATE TABLE news_posts (id INTEGER PRIMARY KEY, publication_state TEXT)'))
        assert await check.safe_to_restore(path)
        async with engine.begin() as conn:
            await conn.execute(text("INSERT INTO news_posts (publication_state) VALUES ('draft')"))
        assert not await check.safe_to_restore(path)
    finally:
        await engine.dispose()
