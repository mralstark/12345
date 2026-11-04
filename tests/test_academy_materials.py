"""Library authorization/downloads, reversible retirement and task compatibility."""
import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from database.models import AcademyBook, AcademyTerm, Member, MemberQuestProgress, Quest, User
from scripts.academy_content import import_catalog
from tests.conftest import login
from utils.data_crypto import FILE_PREFIX

PDF_A = b"%PDF-1.7\nApproved test book A\n%%EOF\n"
PDF_B = b"%PDF-1.7\nApproved test book B\n%%EOF\n"


@pytest.fixture
def material_root(tmp_path, monkeypatch):
    storage = tmp_path / "private"
    storage.mkdir()
    for module in ["scripts.academy_content", "services.academy_materials", "services.report_downloads", "utils.private_files", "utils.storage"]:
        monkeypatch.setattr(module + ".STORAGE_DIR", storage)
    return tmp_path, storage


async def publish(root, session, *, payload=PDF_A):
    source, storage = root
    (source / "book.pdf").write_bytes(payload)
    manifest = source / "catalog.json"
    manifest.write_text(json.dumps({"books": [{"key": "test-book", "title": "Проверочная книга", "file": "book.pdf"}],
                                    "terms": [{"key": "test-term", "term": "Проверочный термин", "definition": "Текст для проверки интерфейса."}]}), encoding="utf-8")
    await import_catalog(manifest, session, apply=True)
    book = (await session.execute(select(AcademyBook))).scalar_one()
    return book, manifest


@pytest.mark.parametrize("role", ["leader_moscow", "leader_tula", "coordinator", "federal", "cell_leader", "superuser"])
async def test_shared_materials_for_all_registered_roles(client, world, session, material_root, role):
    book, _ = await publish(material_root, session)
    login(world[role])
    listed = await client.get("/api/academy/books")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["id"] == book.id
    assert "stored_path" not in listed.json()["items"][0]
    assert (await client.get("/api/academy/vocabulary")).json()["items"][0]["term"] == "Проверочный термин"
    response = await client.get(f"/api/academy/books/{book.id}/download")
    assert response.status_code == 200 and response.content == PDF_A
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert (material_root[1] / book.stored_path).read_bytes().startswith(FILE_PREFIX)


@pytest.mark.parametrize("status", ["activist", "member", "alumni"])
async def test_personal_status_does_not_gate_library(client, session, material_root, world, status):
    book, _ = await publish(material_root, session)
    member = Member(region_id=world["tula"].id, full_name="Читатель", status=status)
    session.add(member); await session.flush()
    user = User(telegram_id=91000, full_name="Читатель", role="participant", member_id=member.id)
    session.add(user); await session.commit()
    login(user)
    assert (await client.get(f"/api/academy/books/{book.id}/download")).content == PDF_A


async def test_anonymous_library_is_closed(client, session, material_root):
    book, _ = await publish(material_root, session)
    for path in ["/api/academy/books", "/api/academy/vocabulary", f"/api/academy/books/{book.id}/download"]:
        assert (await client.get(path)).status_code in (401, 403)
    assert (await client.post("/api/downloads/prepare", json={"kind": "academy_book", "book_id": book.id})).status_code in (401, 403)


async def test_native_ticket_rechecks_account_and_book_revision(client, world, session, material_root):
    book, manifest = await publish(material_root, session)
    login(world["leader_tula"])
    prepared = await client.post("/api/downloads/prepare", json={"kind": "academy_book", "book_id": book.id})
    assert prepared.status_code == 200, prepared.text
    url = prepared.json()["url"]
    assert (await client.get(url)).content == PDF_A
    assert (await client.head(url)).status_code == 200
    await publish(material_root, session, payload=PDF_B)
    assert (await client.get(url)).status_code == 410
    fresh = (await client.post("/api/downloads/prepare", json={"kind": "academy_book", "book_id": book.id})).json()["url"]
    assert (await client.get(fresh)).content == PDF_B
    book.is_active = False; await session.commit()
    assert (await client.get(fresh)).status_code == 404
    book.is_active = True; world["leader_tula"].is_active = False; await session.commit()
    assert (await client.get(fresh)).status_code == 403


async def test_missing_or_escaped_file_cannot_download(client, world, session, material_root):
    book, _ = await publish(material_root, session)
    login(world["superuser"])
    (material_root[1] / book.stored_path).unlink()
    assert (await client.get(f"/api/academy/books/{book.id}/download")).status_code == 404
    assert (await client.post("/api/downloads/prepare", json={"kind": "academy_book", "book_id": book.id})).status_code == 404
    book.stored_path = "../book.pdf"; await session.commit()
    assert (await client.get(f"/api/academy/books/{book.id}/download")).status_code == 404


async def test_import_is_idempotent_validates_whole_batch_and_preserves_omissions(session, material_root):
    book, manifest = await publish(material_root, session)
    original_id, original_path = book.id, book.stored_path
    await import_catalog(manifest, session, apply=True)
    assert (await session.execute(select(AcademyBook))).scalar_one().id == original_id
    assert book.stored_path == original_path
    data = json.loads(manifest.read_text()); data["books"][0]["title"] = "Не должно сохраниться"
    data["books"].append({"key": "bad", "title": "Bad", "file": "missing.pdf"})
    manifest.write_text(json.dumps(data))
    with pytest.raises(FileNotFoundError):
        await import_catalog(manifest, session, apply=True)
    await session.refresh(book)
    assert book.title == "Проверочная книга"
    manifest.write_text(json.dumps({"terms": [{"key": "second", "term": "Тест", "definition": "Тест"}]}))
    assert not (await import_catalog(manifest, session))["applied"]
    assert len((await session.execute(select(AcademyTerm))).scalars().all()) == 1
    await import_catalog(manifest, session, apply=True)
    assert (await session.execute(select(AcademyBook))).scalar_one().id == original_id


async def test_retirement_blocks_learning_keeps_rewards_and_work_tasks(client, world, session, monkeypatch):
    for module in ["services.academy", "api.routers.character", "api.routers.context", "api.routers.quests"]:
        monkeypatch.setattr(module + ".ACADEMY_LEGACY_READ_ONLY", True)
    member = Member(region_id=world["moscow"].id, full_name="Участник", status="activist", stars=10)
    session.add(member); await session.flush()
    student = User(telegram_id=92000, full_name="Участник", role="participant", member_id=member.id)
    quest = Quest(title="Сохранённое задание", emoji="", thresholds="1,3", rewards="", position=0)
    session.add_all([student, quest]); await session.flush()
    progress = MemberQuestProgress(member_id=member.id, quest_id=quest.id, count=1, pending_count=1, stars_claimed=0)
    session.add(progress); await session.commit()
    login(student)
    assert (await client.get("/api/character/me")).json()["academy_enabled"] is False
    assert (await client.post(f"/api/character/me/quests/{quest.id}/submit", json={})).status_code == 409
    claim = await client.post(f"/api/character/me/quests/{quest.id}/claim")
    assert claim.status_code == 200 and claim.json()["stars"] == 11
    login(world["leader_moscow"])
    for action in ["assign", "increment", "decrement", "reject", "cancel"]:
        assert (await client.post(f"/api/members/{member.id}/quests/{quest.id}/{action}", json={})).status_code == 409
    assignees = (await client.get("/api/tasks/assignees")).json()["items"]
    assert any(p["id"] == student.id and p["member_id"] == member.id for p in assignees)
    made = await client.post("/api/tasks", json={"to_user_id":student.id,"title":"Рабочая задача"})
    assert made.status_code == 200
    task_id = made.json()["id"]
    login(student)
    assert (await client.post(f"/api/tasks/{task_id}/status", json={"status":"done"})).status_code == 403
    assert (await client.post(f"/api/tasks/{task_id}/status", json={"status":"review"})).status_code == 200
    login(world["leader_moscow"])
    assert (await client.post(f"/api/tasks/{task_id}/status", json={"status":"done"})).status_code == 200
    assert (await client.get("/api/tasks?box=outbox")).json()["items"][0]["status"] == "done"
    denied = await client.post("/api/tasks", json={"to_user_id":world["leader_tula"].id,"title":"Чужой регион"})
    assert denied.status_code == 403
    await session.refresh(progress)
    assert progress.count == 1 and progress.pending_count == 1
