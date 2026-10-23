"""Security regressions with synthetic accounts and no Telegram network."""
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from database.models import Category, Event, MembershipApplication, Transaction, UniversityCell, User
from handlers import apply
from tests.conftest import login


def callback(user, data):
    return SimpleNamespace(from_user=SimpleNamespace(id=user.telegram_id,full_name=user.full_name),
        data=data,answer=AsyncMock(),message=SimpleNamespace(edit_text=AsyncMock()),bot=None)


async def application(session, world):
    row=MembershipApplication(region_id=world['moscow'].id,telegram_id=991122,
        full_name='Тестовый заявитель',faculty='Исходный факультет')
    session.add(row)
    await session.commit()
    return row


async def test_inactive_reviewer_cannot_approve_an_account(session, world, monkeypatch):
    row=await application(session,world)
    reviewer=world['federal']
    reviewer.is_active=False
    await session.commit()
    welcome=AsyncMock()
    monkeypatch.setattr(apply,'send_cabinet_welcome',welcome)
    await apply.approve_application_button(callback(reviewer,f'apply:approve:{row.id}'))
    await session.refresh(row)
    assert row.state=='pending'
    assert not (await session.execute(select(User).where(User.telegram_id==row.telegram_id))).scalars().all()
    welcome.assert_not_awaited()


async def test_ordinary_user_cannot_open_application_back_button(session, world):
    row=await application(session,world)
    user=User(full_name='Обычный участник',role='participant',telegram_id=991133)
    session.add(user)
    await session.commit()
    event=callback(user,f'apply:back:{row.id}')
    await apply.back_to_review(event)
    event.message.edit_text.assert_not_awaited()
    event.answer.assert_awaited()


@pytest.mark.parametrize('revocation',['inactive','role','scope','reviewed'])
async def test_pending_application_edit_rechecks_authority_at_submission(session, world, revocation):
    row=await application(session,world)
    user=world['leader_moscow']
    state=SimpleNamespace(get_data=AsyncMock(return_value={'application_id':row.id,'field':'faculty'}),clear=AsyncMock())
    if revocation=='inactive':
        user.is_active=False
    elif revocation=='role':
        user.role='participant'
    elif revocation=='scope':
        world['moscow'].leader_user_id=None
    else:
        row.state='approved'
    await session.commit()
    message=SimpleNamespace(from_user=SimpleNamespace(id=user.telegram_id,full_name=user.full_name),
        text='Несанкционированная правка',answer=AsyncMock())
    await apply.edit_field_apply(message,state)
    await session.refresh(row)
    assert row.faculty=='Исходный факультет'
    state.clear.assert_awaited()


@pytest.mark.parametrize('method',['post','patch'])
@pytest.mark.parametrize('event_scope',['sibling','region'])
async def test_cell_leader_cannot_associate_transaction_with_foreign_event(client, session, world, method, event_scope):
    sibling=UniversityCell(region_id=world['moscow'].id,name='Другая ячейка')
    session.add(sibling)
    await session.flush()
    event=Event(region_id=world['moscow'].id,cell_id=sibling.id if event_scope=='sibling' else None,
        title='Закрытое мероприятие',date=date.today())
    own=Transaction(region_id=world['moscow'].id,cell_id=world['mgimo'].id,amount=100,type='expense',date=date.today())
    session.add_all([event,own])
    await session.commit()
    login(world['cell_leader'])
    if method=='post':
        response=await client.post('/api/finance/transactions',json={'region_id':world['moscow'].id,
            'amount':200,'type':'expense','event_id':event.id})
    else:
        response=await client.patch(f'/api/finance/transactions/{own.id}',json={'event_id':event.id})
    assert response.status_code==403,response.text
    await session.refresh(own)
    assert own.event_id is None
    assert not (await session.execute(select(Transaction).where(Transaction.event_id==event.id))).scalars().all()


@pytest.mark.parametrize('method',['post','patch','delete'])
async def test_cell_leader_cannot_manage_shared_regional_categories(client, session, world, method):
    category=Category(region_id=world['moscow'].id,name='Общая категория',type='expense')
    session.add(category)
    await session.flush()
    rows=[Transaction(region_id=world['moscow'].id,cell_id=cell_id,category_id=category.id,
        amount=100,type='expense',date=date.today()) for cell_id in [world['mgimo'].id,None]]
    session.add_all(rows)
    await session.commit()
    login(world['cell_leader'])
    if method=='post':
        response=await client.post('/api/finance/categories',json={'region_id':world['moscow'].id,'name':'Внешняя','type':'expense'})
    elif method=='patch':
        response=await client.patch(f'/api/finance/categories/{category.id}',json={'name':'Внешняя'})
    else:
        response=await client.delete(f'/api/finance/categories/{category.id}')
    assert response.status_code==403,response.text
    await session.refresh(category)
    assert category.name=='Общая категория'
    for row in rows:
        await session.refresh(row)
        assert row.category_id==category.id


async def test_own_cell_event_and_regional_category_management_still_work(client, session, world):
    event=Event(region_id=world['moscow'].id,cell_id=world['mgimo'].id,title='Своя встреча',date=date.today())
    session.add(event)
    await session.commit()
    login(world['cell_leader'])
    response=await client.post('/api/finance/transactions',json={'region_id':world['moscow'].id,
        'amount':200,'type':'expense','event_id':event.id})
    assert response.status_code==200,response.text
    login(world['leader_moscow'])
    response=await client.post('/api/finance/categories',json={'region_id':world['moscow'].id,'name':'Категория руководителя','type':'expense'})
    assert response.status_code==200,response.text
    assert (await client.delete('/api/finance/categories/'+str(response.json()['id']))).status_code==200


@pytest.mark.parametrize("operation", ["list", "upload", "download", "delete", "prepare"])
async def test_cell_leader_cannot_access_regional_documents(client, session, world, operation):
    login(world["leader_moscow"])
    made = await client.post("/api/documents", data={"region_id": str(world["moscow"].id), "title": "Региональный файл"},
                             files={"file": ("report.txt", b"private regional content", "text/plain")})
    assert made.status_code == 200, made.text
    document_id = made.json()["id"]
    login(world["cell_leader"])
    if operation == "list":
        response = await client.get(f"/api/documents?region_id={world['moscow'].id}")
    elif operation == "upload":
        response = await client.post("/api/documents", data={"region_id": str(world["moscow"].id), "title": "Чужая область"},
                                     files={"file": ("report.txt", b"test", "text/plain")})
    elif operation == "prepare":
        response = await client.post("/api/downloads/prepare", json={"kind": "document", "document_id": document_id})
    else:
        url = f"/api/documents/{document_id}" + ("/download" if operation == "download" else "")
        response = await getattr(client, "get" if operation == "download" else "delete")(url)
    assert response.status_code == 403, response.text


async def test_document_ticket_rechecks_downgraded_role(client, session, world):
    login(world["leader_moscow"])
    made = await client.post("/api/documents", data={"region_id": str(world["moscow"].id), "title": "Документ"},
                             files={"file": ("report.txt", b"private", "text/plain")})
    prepared = await client.post("/api/downloads/prepare", json={"kind": "document", "document_id": made.json()["id"]})
    assert prepared.status_code == 200, prepared.text
    leader = world["leader_moscow"]
    leader.role = "cell_leader"
    world["mgimo"].leader_user_id = leader.id
    await session.commit()
    response = await client.get(prepared.json()["url"])
    assert response.status_code == 403, response.text


@pytest.mark.parametrize("operation", ["news", "preview", "article_create", "article_save"])
async def test_cell_leader_cannot_claim_regional_publication_identity(client, session, world, operation):
    login(world["cell_leader"])
    region_id = world["moscow"].id
    if operation == "news":
        response = await client.post("/api/news", data={"text": "Региональное объявление", "official": "true", "region_id": region_id})
    elif operation == "preview":
        response = await client.get(f"/api/news/byline?official=true&region_id={region_id}")
    elif operation == "article_create":
        response = await client.post("/api/news/articles", json={"official": True, "region_id": region_id})
    else:
        made = await client.post("/api/news/articles", json={"official": True})
        assert made.status_code == 200, made.text
        article = made.json()
        response = await client.put(f"/api/news/articles/{article['id']}",
            json={"revision": article["revision"], "title": "Статья", "official": True, "region_id": region_id,
                  "content": [{"tag": "p", "children": ["Текст статьи"]}]})
    assert response.status_code == 403, response.text


async def test_article_publish_rechecks_regional_identity_after_role_change(client, session, world):
    login(world["leader_moscow"])
    made = await client.post("/api/news/articles", json={"official": True, "region_id": world["moscow"].id})
    a = made.json()
    saved = await client.put(f"/api/news/articles/{a['id']}", json={"revision": a["revision"], "title": "Статья",
        "official": True, "region_id": world["moscow"].id, "content": [{"tag": "p", "children": ["Текст статьи"]}]})
    assert saved.status_code == 200, saved.text
    leader = world["leader_moscow"]
    leader.role = "cell_leader"
    world["mgimo"].leader_user_id = leader.id
    await session.commit()
    response = await client.post(f"/api/news/articles/{a['id']}/publish", json={"revision": saved.json()["revision"]})
    assert response.status_code == 403, response.text


@pytest.mark.parametrize("encoding", ["entity", "utf16", "doctype", "malformed"])
async def test_office_relationships_are_checked_as_xml(client, world, encoding):
    from tests.test_api import _docx
    login(world["leader_moscow"])
    xml = '<Relationships><Relationship TargetMode="External" Target="https://example.invalid/never-requested"/></Relationships>'
    if encoding == "entity":
        payload = xml.replace("External", "Exter&#110;al").encode()
    elif encoding == "utf16":
        payload = ('<?xml version="1.0" encoding="UTF-16"?>' + xml).encode("utf-16")
    elif encoding == "doctype":
        payload = ('<!DOCTYPE Relationships [<!ENTITY mode "External">]>' + xml.replace("External", "&mode;")).encode()
    else:
        payload = b"<Relationships"
    response = await client.post("/api/documents", data={"region_id": str(world["moscow"].id), "title": "Файл"},
        files={"file": ("report.docx", _docx("word/_rels/document.xml.rels", payload), "application/octet-stream")})
    assert response.status_code == 400, response.text


@pytest.mark.parametrize("disabled", [False, True])
@pytest.mark.parametrize("kind", ["message", "callback"])
async def test_bot_dispatch_rechecks_real_account_before_handlers(session, world, disabled, kind):
    from aiogram import Bot, Dispatcher, Router
    from aiogram.types import Update
    from datetime import datetime, timezone
    from utils.bot_security import ActiveBotUserMiddleware
    actor = world["superuser"]
    actor.is_active = not disabled
    actor.view_as_user_id = world["federal"].id
    await session.commit()
    dp = Dispatcher()
    dp.message.outer_middleware(ActiveBotUserMiddleware())
    dp.callback_query.outer_middleware(ActiveBotUserMiddleware())
    router = Router()
    reached = AsyncMock()
    async def handle(event): await reached()
    router.message.register(handle)
    router.callback_query.register(handle)
    dp.include_router(router)
    bot = Bot("123456:TEST-TOKEN-FOR-TESTS")
    payload = {"id": actor.telegram_id, "is_bot": False, "first_name": "Test"}
    message = {"message_id": 1, "date": int(datetime.now(timezone.utc).timestamp()),
               "chat": {"id": actor.telegram_id, "type": "private"}, "from": payload, "text": "/start"}
    body = {"message": message} if kind == "message" else {"callback_query": {
        "id": "security-test", "from": payload, "chat_instance": "test", "data": "apply:list", "message": message}}
    # No Telegram calls; the dispatcher and middleware chain are real.
    bot.session.make_request = AsyncMock(return_value=True)
    try:
        await dp.feed_update(bot, Update.model_validate({"update_id": 1, **body}))
        assert reached.await_count == (0 if disabled else 1)
        assert bot.session.make_request.await_count == (1 if disabled else 0)
    finally:
        await bot.session.close()


async def test_bot_unknown_identity_can_still_reach_registration():
    from utils.bot_security import ActiveBotUserMiddleware
    event = SimpleNamespace(from_user=SimpleNamespace(id=775599), answer=AsyncMock())
    handler = AsyncMock(return_value="registration")
    assert await ActiveBotUserMiddleware()(handler, event, {}) == "registration"
    handler.assert_awaited_once()


@pytest.mark.parametrize("scope", ["region", "cell"])
async def test_archiving_closes_non_superuser_write_access(client, session, world, scope):
    if scope == "region":
        actor = world["leader_moscow"]
        world["moscow"].is_active = False
    else:
        actor = world["cell_leader"]
        world["mgimo"].is_active = False
    await session.commit()
    login(actor)
    response = await client.post("/api/finance/transactions", json={
        "region_id": world["moscow"].id, "amount": 100, "type": "expense"})
    assert response.status_code == 403, response.text


async def test_internal_office_relationships_remain_supported(client, world):
    from tests.test_api import _docx
    login(world["leader_moscow"])
    relation = b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="media/image1.png" Type="image"/></Relationships>'
    response = await client.post("/api/documents", data={"region_id": str(world["moscow"].id), "title": "Обычный документ"},
        files={"file": ("report.docx", _docx("word/_rels/document.xml.rels", relation), "application/octet-stream")})
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("role", ["cell_leader", "leader_moscow", "coordinator", "federal", "superuser"])
async def test_allowed_publication_signatures_remain_available(client, world, role):
    login(world[role])
    params = {"official": "true"}
    if role != "cell_leader":
        params["region_id"] = world["moscow"].id
    response = await client.get("/api/news/byline", params=params)
    assert response.status_code == 200, response.text
    expected = world["mgimo"].name if role == "cell_leader" else world["moscow"].name
    assert response.json()["byline"] == "Академисты | " + expected


@pytest.mark.parametrize("suffix", ["xlsx", "pdf", "csv", "docx", "pptx", "txt"])
async def test_export_formats_roundtrip_with_unicode_name(client, world, tmp_path, monkeypatch, suffix):
    from services import report_downloads as exports
    monkeypatch.setattr(exports, "STORAGE_DIR", tmp_path)
    ticket = await exports.save_export(b"synthetic export", world["leader_moscow"].id, world["moscow"].id,
                                      suffix, "Отчёт." + suffix)
    file_id = exports.decode_ticket(ticket)["file"]
    response = await client.get(f"/api/reports/files/{file_id}", params={"t": ticket})
    assert response.status_code == 200, response.text
    assert response.content == b"synthetic export"
    assert "filename*=UTF-8" in response.headers["content-disposition"]
    head = await client.head(f"/api/reports/files/{file_id}", params={"t": ticket})
    assert head.status_code == 200 and head.content == b""


@pytest.mark.parametrize("suffix", ["../txt", "/tmp/export", "txt/../../escape", "TXT", "", "txt\\escape"])
async def test_invalid_export_format_fails_before_filesystem(tmp_path, monkeypatch, suffix):
    from fastapi import HTTPException
    from services import report_downloads as exports
    location = tmp_path / "never-created"
    monkeypatch.setattr(exports, "STORAGE_DIR", location)
    with pytest.raises(HTTPException) as error:
        await exports.save_export(b"x", 1, 1, suffix, "display.txt")
    assert error.value.status_code == 400
    assert not location.exists()


@pytest.mark.parametrize("filename", ["../" + "a"*48 + ".txt", "a"*48 + ".txt/../outside", "a"*48 + ".txt\\outside"])
def test_export_boundary_rejects_traversal(tmp_path, monkeypatch, filename):
    from fastapi import HTTPException
    from services import report_downloads as exports
    monkeypatch.setattr(exports, "STORAGE_DIR", tmp_path)
    with pytest.raises(HTTPException):
        exports.export_path(filename)


async def test_export_boundary_rejects_outside_symlink(client, world, tmp_path, monkeypatch):
    from services import report_downloads as exports
    monkeypatch.setattr(exports, "STORAGE_DIR", tmp_path)
    ticket = await exports.save_export(b"safe", world["leader_moscow"].id, world["moscow"].id, "txt", "display.txt")
    file_id = exports.decode_ticket(ticket)["file"]
    path = exports.export_path(file_id)
    outside = tmp_path / "outside-private.txt"
    outside.write_bytes(b"private")
    path.unlink()
    try:
        path.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("Creating symlinks is not permitted on this host")
    response = await client.get(f"/api/reports/files/{file_id}", params={"t": ticket})
    assert response.status_code == 403


@pytest.mark.parametrize("working_copy", [False, True])
@pytest.mark.parametrize("role_before,role_after", [("federal", "leader"), ("leader", "cell_leader")])
async def test_saved_implicit_official_identity_cannot_survive_role_change(client, session, world, working_copy, role_before, role_after):
    from database.models import NewsPost
    actor = world["federal"] if role_before == "federal" else world["leader_moscow"]
    login(actor)
    a = (await client.post("/api/news/articles", json={"official": True})).json()
    saved = await client.put(f"/api/news/articles/{a['id']}", json={"revision": a["revision"], "title": "Объявление",
        "official": True, "content": [{"tag": "p", "children": ["Официальный текст"]}]})
    assert saved.status_code == 200, saved.text
    a = saved.json()
    source_id = None
    if working_copy:
        published = await client.post(f"/api/news/articles/{a['id']}/publish", json={"revision": a["revision"]})
        assert published.status_code == 200, published.text
        source_id = published.json()["id"]
        a = (await client.post(f"/api/news/articles/{source_id}/edit")).json()
    actor.role = role_after
    actor.is_federal = False
    actor.is_coordinator = False
    if role_after == "leader":
        world["moscow"].leader_user_id = actor.id
    else:
        world["mgimo"].leader_user_id = actor.id
    await session.commit()
    if source_id:
        response = await client.post(f"/api/news/articles/{source_id}/edit")
        assert response.status_code == 403, response.text
        response = await client.put(f"/api/news/articles/{a['id']}", json={"revision": a["revision"], "title": "Изменённый текст"})
        assert response.status_code == 403, response.text
    response = await client.post(f"/api/news/articles/{a['id']}/publish", json={"revision": a["revision"]})
    assert response.status_code == 403, response.text
    posts = list((await session.execute(select(NewsPost).where(NewsPost.publication_state == "published"))).scalars())
    assert len(posts) == (1 if working_copy else 0)


@pytest.mark.parametrize("role,archived", [("leader_moscow", "region"), ("cell_leader", "region"), ("cell_leader", "cell")])
async def test_archived_scope_cannot_publish_implicit_official_news(client, session, world, role, archived):
    if archived == "region":
        world["moscow"].is_active = False
    else:
        world["mgimo"].is_active = False
    await session.commit()
    login(world[role])
    response = await client.post("/api/news", data={"text": "Архивная публикация", "official": "true"})
    assert response.status_code == 403, response.text
