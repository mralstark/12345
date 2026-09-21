"""Сквозные проверки API Mini App: каждый модуль ТЗ по разу, плюс отказы в доступе."""

import io
import zipfile
from datetime import timedelta

from PIL import Image
from sqlalchemy import select

from config import STORAGE_DIR
from database.models import (
    ROLE_CELL_LEADER,
    ROLE_PARTICIPANT,
    Member,
    ShopPurchase,
    UniversityCell,
    User,
)
from services.images import PHOTO_MAX_SIDE
from tests.conftest import login
from utils.tz import today as tz_today


def _tiny_jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), "white").save(buffer, "JPEG")
    return buffer.getvalue()


def _docx(extra_name: str | None = None, extra_payload: bytes = b"") -> bytes:
    buffer = io.BytesIO()
    mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", f'<Types><Override ContentType="{mime}"/></Types>')
        archive.writestr("word/document.xml", "<document/>")
        if extra_name:
            archive.writestr(extra_name, extra_payload)
    return buffer.getvalue()


async def test_security_headers_are_applied(client):
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "default-src 'self'" in response.headers["content-security-policy"]


async def test_untrusted_host_is_rejected(client):
    response = await client.get("/healthz", headers={"Host": "attacker.example"})
    assert response.status_code == 400


async def test_me_lists_only_accessible_regions(client, world):
    login(world["coordinator"])
    response = await client.get("/api/me")
    assert response.status_code == 200
    data = response.json()
    assert [r["name"] for r in data["regions"]] == ["Москва"]
    assert data["editable_region_ids"] == []  # координатор — только просмотр
    assert data["is_supervisor"] is True


async def test_members_crud_and_search(client, world):
    login(world["leader_moscow"])
    region_id = world["moscow"].id

    created = await client.post(
        "/api/members",
        json={
            "region_id": region_id,
            "full_name": "Смирнов Алексей",
            "status": "member",
            "phone": "+7 900 000-00-00",
            "birth_date": "2001-05-14",
            "member_inducted_at": "2023-09-01",
        },
    )
    assert created.status_code == 200, created.text
    member_id = created.json()["id"]

    listed = await client.get(f"/api/members?region_id={region_id}&q=смирнов")
    assert [item["id"] for item in listed.json()["items"]] == [member_id]
    assert listed.json()["total"] == 1

    patched = await client.patch(
        f"/api/members/{member_id}", json={"status": "alumni", "alumni_graduated_at": "2026-06-30"}
    )
    assert patched.json()["status_label"] == "Выпускник"

    filtered = await client.get(f"/api/members?region_id={region_id}&status=member")
    assert filtered.json()["items"] == []

    deleted = await client.delete(f"/api/members/{member_id}")
    assert deleted.status_code == 200
    assert (await client.get(f"/api/members?region_id={region_id}")).json()["items"] == []


async def test_member_milestone_dates_required_for_matching_status(client, world):
    """Присваивая статус «член Братства»/«выпускник», руководитель обязан
    заполнить соответствующую веху (Посвящение/Выпуск) — план «3 даты»."""
    login(world["leader_moscow"])
    region_id = world["moscow"].id

    missing_induction = await client.post(
        "/api/members",
        json={"region_id": region_id, "full_name": "Без Посвящения", "status": "member"},
    )
    assert missing_induction.status_code == 400

    missing_graduation = await client.post(
        "/api/members",
        json={"region_id": region_id, "full_name": "Без Выпуска", "status": "alumni"},
    )
    assert missing_graduation.status_code == 400

    activist = await client.post(
        "/api/members",
        json={"region_id": region_id, "full_name": "Просто Активист", "status": "activist"},
    )
    assert activist.status_code == 200, activist.text
    member_id = activist.json()["id"]

    still_missing = await client.patch(f"/api/members/{member_id}", json={"status": "member"})
    assert still_missing.status_code == 400

    now_ok = await client.patch(
        f"/api/members/{member_id}", json={"status": "member", "member_inducted_at": "2024-05-01"}
    )
    assert now_ok.status_code == 200
    assert now_ok.json()["member_inducted_at"] == "2024-05-01"


async def test_coordinator_cannot_create_members(client, world):
    login(world["coordinator"])
    response = await client.post(
        "/api/members", json={"region_id": world["moscow"].id, "full_name": "Кто-то Ещё"}
    )
    assert response.status_code == 403


async def test_leader_cannot_touch_foreign_region(client, world):
    login(world["leader_moscow"])
    assert (await client.get(f"/api/members?region_id={world['tula'].id}")).status_code == 403
    assert (await client.get(f"/api/dashboard?region_id={world['tula'].id}")).status_code == 403


async def test_finance_flow_and_balance(client, world):
    login(world["leader_moscow"])
    region_id = world["moscow"].id

    categories = await client.get(f"/api/finance/categories?region_id={region_id}")
    assert categories.json()["items"], "региону должен создаться стартовый справочник"
    expense_category = next(c for c in categories.json()["items"] if c["type"] == "expense")

    await client.post(
        "/api/finance/transactions",
        json={"region_id": region_id, "amount": 500000, "type": "income", "date": "2026-07-10"},
    )
    await client.post(
        "/api/finance/transactions",
        json={
            "region_id": region_id,
            "amount": 120000,
            "type": "expense",
            "category_id": expense_category["id"],
            "date": "2026-07-12",
            "comment": "Бумага",
        },
    )

    overview = await client.get(f"/api/finance/overview?region_id={region_id}&period=all")
    data = overview.json()
    assert data["income"] == 500000
    assert data["expense"] == 120000
    assert data["balance"] == 380000

    transactions = await client.get(f"/api/finance/transactions?region_id={region_id}&period=all")
    assert len(transactions.json()["items"]) == 2

    csv = await client.get(f"/api/finance/export.csv?region_id={region_id}&period=all")
    assert csv.status_code == 200
    assert "Бумага" in csv.content.decode("utf-8")


async def test_finance_rejects_cell_from_another_region(client, session, world):
    foreign_cell = UniversityCell(region_id=world["tula"].id, name="Тульская ячейка")
    session.add(foreign_cell)
    await session.commit()
    await session.refresh(foreign_cell)

    login(world["leader_moscow"])
    response = await client.post(
        "/api/finance/transactions",
        json={
            "region_id": world["moscow"].id,
            "amount": 1000,
            "type": "income",
            "cell_id": foreign_cell.id,
        },
    )
    assert response.status_code == 400


async def test_coordinator_sees_finance_read_only(client, world):
    login(world["leader_moscow"])
    region_id = world["moscow"].id
    await client.post(
        "/api/finance/transactions", json={"region_id": region_id, "amount": 1000, "type": "income"}
    )

    login(world["coordinator"])
    assert (await client.get(f"/api/finance/overview?region_id={region_id}")).json()["balance"] == 1000
    denied = await client.post(
        "/api/finance/transactions", json={"region_id": region_id, "amount": 1000, "type": "income"}
    )
    assert denied.status_code == 403


async def test_event_budget_plan_vs_fact_and_attendance(client, world):
    login(world["leader_moscow"])
    region_id = world["moscow"].id

    member = (await client.post(
        "/api/members", json={"region_id": region_id, "full_name": "Кузнецов Иван"}
    )).json()

    event = (await client.post(
        "/api/events",
        json={
            "region_id": region_id,
            "title": "Форум",
            "date": "2026-08-01",
            "description": "Описание",
            "planned_budget": 300000,
        },
    )).json()

    await client.post(
        "/api/finance/transactions",
        json={
            "region_id": region_id,
            "amount": 250000,
            "type": "expense",
            "event_id": event["id"],
            "date": "2026-08-01",
        },
    )

    card = (await client.get(f"/api/events/{event['id']}")).json()
    assert card["fact_expense"] == 250000
    assert card["budget_diff"] == 50000

    attendance = await client.put(
        f"/api/events/{event['id']}/attendance", json={"member_ids": [member["id"]]}
    )
    assert attendance.json()["attended"] == [member["id"]]

    unmark = await client.put(f"/api/events/{event['id']}/attendance", json={"member_ids": []})
    assert unmark.json()["attended"] == []


async def test_recurring_event_materializes_series(client, world):
    login(world["leader_moscow"])
    region_id = world["moscow"].id

    created = await client.post(
        "/api/events",
        json={
            "region_id": region_id,
            "title": "Еженедельная встреча",
            "date": "2026-08-03",
            "description": "Описание",
            "is_recurring": True,
            "recurrence_rule": "weekly",
            "recurrence_until": "2026-08-31",
        },
    )
    assert created.status_code == 200, created.text

    listed = (await client.get(f"/api/events?region_id={region_id}&scope=all")).json()["items"]
    dates = sorted(item["date"] for item in listed)
    assert dates == ["2026-08-03", "2026-08-10", "2026-08-17", "2026-08-24", "2026-08-31"]


async def test_documents_upload_and_download(client, world):
    login(world["leader_moscow"])
    region_id = world["moscow"].id

    upload = await client.post(
        "/api/documents",
        data={"region_id": str(region_id), "title": "Устав", "doc_type": "Устав и положения"},
        files={"file": ("устав.txt", b"content of the charter", "text/plain")},
    )
    assert upload.status_code == 200, upload.text
    document_id = upload.json()["id"]

    listed = await client.get(f"/api/documents?region_id={region_id}")
    assert listed.json()["items"][0]["title"] == "Устав"

    downloaded = await client.get(f"/api/documents/{document_id}/download")
    assert downloaded.content == b"content of the charter"

    # Координатор видит документ, но удалить не может.
    login(world["coordinator"])
    assert (await client.get(f"/api/documents?region_id={region_id}")).status_code == 200
    assert (await client.delete(f"/api/documents/{document_id}")).status_code == 403


async def test_documents_reject_spoofed_or_executable_upload(client, world):
    login(world["leader_moscow"])
    region_id = world["moscow"].id

    fake_pdf = await client.post(
        "/api/documents",
        data={"region_id": str(region_id), "title": "Не PDF"},
        files={"file": ("report.pdf", b"MZ executable", "application/pdf")},
    )
    assert fake_pdf.status_code == 400

    executable = await client.post(
        "/api/documents",
        data={"region_id": str(region_id), "title": "Программа"},
        files={"file": ("run.exe", b"MZ executable", "application/octet-stream")},
    )
    assert executable.status_code == 400


async def test_documents_reject_active_office_content(client, world):
    login(world["leader_moscow"])
    data = {"region_id": str(world["moscow"].id), "title": "Опасный документ"}

    macro = await client.post(
        "/api/documents",
        data=data,
        files={"file": ("report.docx", _docx("word/vbaProject.bin", b"macro"), "application/octet-stream")},
    )
    assert macro.status_code == 400

    relation = b'<Relationships><Relationship TargetMode="External" Target="https://example.test"/></Relationships>'
    external = await client.post(
        "/api/documents",
        data=data,
        files={"file": ("report.docx", _docx("word/_rels/document.xml.rels", relation), "application/octet-stream")},
    )
    assert external.status_code == 400


async def test_task_lifecycle(client, world):
    login(world["coordinator"])
    # Срок считаем от сегодня, а не записываем числом: «Новая» — это статус
    # задачи, срок которой ещё не наступил. С датой, записанной как будущая
    # однажды, тест сам себя ломает в день, когда она становится прошлым, —
    # так и вышло, он падал с «Просрочена» вместо «Новая».
    deadline = (tz_today() + timedelta(days=30)).isoformat()
    created = await client.post(
        "/api/tasks",
        json={"to_user_id": world["leader_moscow"].id, "title": "Сдать отчёт", "deadline": deadline},
    )
    assert created.status_code == 200
    task_id = created.json()["id"]

    login(world["leader_moscow"])
    inbox = (await client.get("/api/tasks?box=inbox")).json()["items"]
    assert inbox[0]["status_label"] == "Новая"

    in_progress = await client.post(f"/api/tasks/{task_id}/status", json={"status": "in_progress"})
    assert in_progress.json()["status"] == "in_progress"
    done = await client.post(f"/api/tasks/{task_id}/status", json={"status": "done"})
    assert done.json()["status_label"] == "Выполнена"

    # Отменённого статуса у задачи нет вовсе: снятая задача удаляется.
    unknown = await client.post(f"/api/tasks/{task_id}/status", json={"status": "cancelled"})
    assert unknown.status_code == 400

    # Снять задачу может только тот, кто её поставил.
    forbidden = await client.delete(f"/api/tasks/{task_id}")
    assert forbidden.status_code == 403

    login(world["coordinator"])
    assert (await client.delete(f"/api/tasks/{task_id}")).status_code == 200
    login(world["leader_moscow"])
    assert (await client.get("/api/tasks?box=inbox")).json()["items"] == []


async def test_overdue_status_is_derived_from_deadline(client, world):
    login(world["coordinator"])
    created = await client.post(
        "/api/tasks",
        json={"to_user_id": world["leader_moscow"].id, "title": "Просроченная", "deadline": "2000-01-01"},
    )
    assert created.json()["effective_status"] == "overdue"


async def _participant(session, region_id, name, telegram_id, cell_id=None):
    """Участник с карточкой в составе — аудитория новостей считается по ней."""
    member = Member(region_id=region_id, full_name=name, cell_id=cell_id)
    session.add(member)
    await session.flush()
    user = User(full_name=name, role=ROLE_PARTICIPANT, member_id=member.id, telegram_id=telegram_id)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def _cell_leader(session, region_id, cell_name, name, telegram_id):
    """Руководитель ячейки — теперь единственный, кто пишет в ленту снизу.

    Раньше в этих проверках авторами были участники: писать мог любой. Теперь
    пост в ленте всегда чей-то официальный, и модерация — это разбор между
    уровнями руководства, а не между руководителем и участником.
    """
    member = Member(region_id=region_id, full_name=name)
    session.add(member)
    await session.flush()
    user = User(full_name=name, role=ROLE_CELL_LEADER, member_id=member.id, telegram_id=telegram_id)
    session.add(user)
    await session.flush()
    cell = UniversityCell(region_id=region_id, name=cell_name, leader_user_id=user.id)
    session.add(cell)
    await session.flush()
    member.cell_id = cell.id
    await session.commit()
    await session.refresh(user)
    return user


async def test_feed_is_shared_by_everyone(client, world, session):
    """Лента общая: разделения по отделениям нет, все видят всё."""
    moscow_user = await _participant(session, world["moscow"].id, "Москвич", 2001)
    tula_user = await _participant(session, world["tula"].id, "Туляк", 2002)

    login(world["leader_moscow"])
    assert (await client.post("/api/news", data={"text": "Сбор москвичей"})).status_code == 200
    login(world["cell_leader"])
    assert (await client.post("/api/news", data={"text": "Сбор ячейки"})).status_code == 200

    for user in (moscow_user, tula_user):
        login(user)
        texts = [i["text"] for i in (await client.get("/api/news")).json()["items"]]
        assert sorted(texts) == ["Сбор москвичей", "Сбор ячейки"]


async def test_byline_depends_on_cabinet(client, world, session):
    """Подпись решает кабинет, а не роль: из личного руководитель говорит от
    себя, из кабинета управления — от отделения."""
    participant = await _participant(session, world["moscow"].id, "Лебедев Сергей Петрович", 2003)

    cases = [
        (world["federal"], "Братство Академистов", "Фёдоров Фёдор"),
        (world["coordinator"], "Братство Академистов", "Сидоров Семён"),
        (world["leader_moscow"], "Академисты | Москва", "Иванов Иван"),
        (world["cell_leader"], "Академисты | МГИМО", "Кузнецов Иван"),
    ]
    for user, official_byline, personal_byline in cases:
        login(user)
        assert (await client.get("/api/news/byline?official=true")).json()["byline"] == official_byline
        assert (await client.get("/api/news/byline?official=false")).json()["byline"] == personal_byline

        from_region = await client.post("/api/news", data={"text": "От отделения", "official": "true"})
        assert from_region.json()["byline"] == official_byline
        from_personal = await client.post("/api/news", data={"text": "От себя", "official": "false"})
        assert from_personal.json()["byline"] == personal_byline

    # Участнику официальная подпись недоступна ни при каком запросе.
    login(participant)
    assert (await client.get("/api/news/byline?official=true")).json()["can_official"] is False
    assert (await client.post("/api/news", data={"text": "Нельзя", "official": "true"})).status_code == 403


async def test_names_are_surname_and_first_name(client, world, session):
    """В ленте отчества нет — оно только удлиняет подпись.

    Проверяем на личной подписи руководителя: в саму ленту личных постов
    больше не попадает (см. test_only_leaders_post_and_always_officially),
    но short_name по-прежнему режет отчество везде, где имя показывают.
    """
    from services.news import short_name

    assert short_name("Волков Михаил Сергеевич") == "Волков Михаил"

    login(world["leader_moscow"])
    created = await client.post("/api/news", data={"text": "Пост", "official": "false"})
    assert created.json()["byline"] == short_name(world["leader_moscow"].full_name)


async def test_only_leaders_post_and_always_officially(client, world, session):
    """В ленте говорит организация, а не человек.

    Раньше писал любой, а роль решала лишь подпись, — и рядом с объявлением
    отделения стояла личная запись участника. Теперь участнику отказано и в
    кнопке, и в самом запросе, а пост руководителя выходит от отделения.
    """
    member = await _participant(session, world["moscow"].id, "Волков Михаил", 2004)

    login(member)
    assert (await client.get("/api/news")).json()["can_post"] is False
    refused = await client.post("/api/news", data={"text": "Ищу попутчиков"})
    assert refused.status_code == 403

    login(world["leader_moscow"])
    assert (await client.get("/api/news")).json()["can_post"] is True
    created = await client.post("/api/news", data={"text": "Сбор в субботу", "official": "true"})
    assert created.status_code == 200

    login(member)
    item = (await client.get("/api/news")).json()["items"][0]
    assert item["text"] == "Сбор в субботу"
    assert "Академисты" in item["byline"]


async def test_byline_frozen_at_publish(client, world, session):
    """Подпись не переписывается задним числом: автор сменил роль — у старого
    поста атрибуция прежняя."""
    login(world["leader_moscow"])
    created = await client.post("/api/news", data={"text": "От отделения", "official": "true"})
    post_id = created.json()["id"]

    world["leader_moscow"].role = "federal"
    session.add(world["leader_moscow"])
    await session.commit()

    login(world["leader_moscow"])
    items = (await client.get("/api/news")).json()["items"]
    assert next(i for i in items if i["id"] == post_id)["byline"] == "Академисты | Москва"


async def test_news_author_can_delete_own(client, world, session):
    reader = await _participant(session, world["moscow"].id, "Москвич", 2010)
    login(world["leader_moscow"])
    post_id = (await client.post("/api/news", data={"text": "Опечатка"})).json()["id"]

    login(reader)
    assert (await client.delete(f"/api/news/{post_id}")).status_code == 403

    login(world["leader_moscow"])
    assert (await client.delete(f"/api/news/{post_id}")).status_code == 200
    assert (await client.get("/api/news")).json()["items"] == []


async def test_news_photo_needs_signed_link(client, world, session):
    """Ссылка на фотографию подписана: тег <img> ходит без заголовка
    авторизации, поэтому право проверяется подписью, а не сессией."""
    reader = await _participant(session, world["moscow"].id, "Москвич", 2011)
    login(world["leader_moscow"])
    created = await client.post(
        "/api/news",
        data={"text": "Снимки со сбора"},
        files=[("files", ("photo.jpg", _tiny_jpeg(), "image/jpeg"))],
    )
    assert created.json()["photos"] == 1

    login(reader)
    photo_url = (await client.get("/api/news")).json()["items"][0]["photos"][0]["url"]
    assert "?t=" in photo_url
    assert (await client.get(photo_url)).status_code == 200

    bare = photo_url.split("?")[0]
    assert (await client.get(bare)).status_code == 403
    assert (await client.get(bare + "?t=1799999999.deadbeef")).status_code == 403


async def test_photo_link_is_stable_between_renders(client, world, session):
    """Адрес фотографии не должен меняться от отрисовки к отрисовке: браузер
    хранит кэш по адресу, и «свежая» ссылка каждый раз означала, что все
    фотографии качаются заново при каждом открытии ленты."""
    login(world["leader_moscow"])
    await client.post(
        "/api/news",
        data={"text": "Снимки со сбора"},
        files=[("files", ("photo.jpg", _tiny_jpeg(), "image/jpeg"))],
    )

    first = (await client.get("/api/news")).json()["items"][0]
    second = (await client.get("/api/news")).json()["items"][0]

    assert first["photos"][0]["url"] == second["photos"][0]["url"]
    # Аватар в подписи поста подписывается тем же способом и страдал так же.
    assert first["avatar"] == second["avatar"]


async def test_uploaded_photo_is_shrunk(client, world, session):
    """Снимок с телефона не должен ехать к читателю целиком: 4000 точек по
    длинной стороне превращаются в размер, который кабинет реально рисует."""
    import io as _io

    from PIL import Image

    from database.models import NewsPhoto

    buffer = _io.BytesIO()
    Image.effect_mandelbrot((3000, 2000), (-2, -1.5, 1, 1.5), 60).convert("RGB").save(
        buffer, "JPEG", quality=95
    )
    original = buffer.getvalue()

    login(world["leader_moscow"])
    assert (await client.post(
        "/api/news",
        data={"text": "Съезд"},
        files=[("files", ("IMG_0042.HEIC", original, "image/jpeg"))],
    )).status_code == 200

    photo = (await session.execute(select(NewsPhoto))).scalars().one()
    assert photo.size_bytes < len(original)
    # Имя на диске не должно обещать HEIC там, где лежит JPEG.
    assert photo.stored_path.endswith(".jpg")

    stored = (STORAGE_DIR / photo.stored_path).read_bytes()
    assert max(Image.open(_io.BytesIO(stored)).size) == PHOTO_MAX_SIDE


async def test_slow_photo_processing_does_not_block_other_requests(client, world, monkeypatch):
    """Кабинет не должен замирать, пока обрабатывается фотография.

    Сжатие снимка с телефона занимает около четырёх десятых секунды, и пока
    оно шло прямо в общем потоке, всё остальное не обслуживалось: ядро на
    сервере одно, подхватить некому. Здесь подменяем сжатие на заведомо
    медленное и считаем, сколько лёгких запросов успевает пройти, пока идёт
    загрузка. Если сжатие держит общий поток — ни одного.
    """
    import asyncio as _asyncio
    import time as _time

    BLOCK = 0.4

    def slow_shrink(payload, max_side):
        _time.sleep(BLOCK)          # синхронно, как настоящее сжатие
        return payload, "image/jpeg"

    monkeypatch.setattr("services.news.shrink", slow_shrink)
    login(world["leader_moscow"])

    served = 0

    async def ping_until(done):
        nonlocal served
        # Пауза обязательна: без неё опрос не отдаёт управление и загрузка не
        # успевает даже начаться — тест зависает вместо того, чтобы мерить.
        while not done.done():
            await client.get("/healthz")
            served += 1
            await _asyncio.sleep(0.005)

    upload = _asyncio.ensure_future(client.post(
        "/api/news",
        data={"text": "Снимок"},
        files=[("files", ("photo.jpg", _tiny_jpeg(), "image/jpeg"))],
    ))
    await _asyncio.wait_for(ping_until(upload), timeout=10)
    response = await upload

    assert response.status_code == 200
    # Порог с большим запасом: без блокировки за четыре десятых секунды
    # проходят десятки лёгких запросов, с блокировкой — ноль или единицы.
    assert served >= 5, f"пока шла загрузка, обслужено всего {served} запросов — кабинет замирает"


async def test_news_rejects_non_image_upload(client, world):
    login(world["leader_moscow"])
    resp = await client.post(
        "/api/news",
        data={"text": "С документом"},
        files=[("files", ("smeta.pdf", b"%PDF-1.4 fake", "application/pdf"))],
    )
    assert resp.status_code == 400


async def test_news_rejects_spoofed_image_content_type(client, world):
    login(world["leader_moscow"])
    resp = await client.post(
        "/api/news",
        data={"text": "Замаскированный файл"},
        files=[("files", ("photo.jpg", b"%PDF-1.4 fake", "image/jpeg"))],
    )
    assert resp.status_code == 400
    assert "изображени" in resp.json()["detail"]


async def test_comments_closed_reactions_open(client, world, session):
    """Комментарии закрыты, отклик остался.

    Лента — объявления движения, а не обсуждение под ними. Сердце при этом
    живо: оно говорит «прочитал», не втягивая в разговор. Отказ приходит и на
    прямой запрос, а не только прячется кнопка.
    """
    tula_user = await _participant(session, world["tula"].id, "Туляк", 2012)

    login(world["leader_moscow"])
    post_id = (await client.post("/api/news", data={"text": "Сбор", "official": "true"})).json()["id"]

    login(tula_user)
    refused = await client.post(f"/api/news/{post_id}/comments", json={"text": "Придём"})
    assert refused.status_code == 403

    reacted = await client.post(f"/api/news/{post_id}/reactions", json={"emoji": "❤️"})
    assert reacted.json()["reactions"] == [{"emoji": "❤️", "count": 1, "mine": True}]


async def test_old_comments_are_not_shown(client, world, session):
    """Написанное до закрытия из базы не стирали, но в ленту оно не выходит."""
    from services.news import add_comment

    reader = await _participant(session, world["moscow"].id, "Москвич", 2015)
    login(world["leader_moscow"])
    post_id = (await client.post("/api/news", data={"text": "Сбор", "official": "true"})).json()["id"]

    # Мимо закрытого эндпоинта — так, как комментарии попадали в базу раньше.
    await add_comment(session, reader, post_id, "Буду")

    login(reader)
    item = (await client.get("/api/news")).json()["items"][0]
    assert item["comments"] == []


async def test_like_toggles(client, world, session):
    """Реакция одна — лайк: повторное нажатие снимает его."""
    reader = await _participant(session, world["moscow"].id, "Москвич", 2017)
    login(world["leader_moscow"])
    post_id = (await client.post("/api/news", data={"text": "Сбор"})).json()["id"]

    login(reader)
    first = await client.post(f"/api/news/{post_id}/reactions", json={"emoji": "❤️"})
    assert first.json()["reactions"] == [{"emoji": "❤️", "count": 1, "mine": True}]

    second = await client.post(f"/api/news/{post_id}/reactions", json={"emoji": "❤️"})
    assert second.json()["reactions"] == []

    # Прочие эмодзи убраны из набора — сервер их не принимает.
    assert (await client.post(f"/api/news/{post_id}/reactions", json={"emoji": "🔥"})).status_code == 400


async def test_views_count_unique_readers(client, world, session):
    reader = await _participant(session, world["moscow"].id, "Москвич", 2018)
    other = await _participant(session, world["moscow"].id, "Другой москвич", 2019)
    login(world["leader_moscow"])
    post_id = (await client.post("/api/news", data={"text": "Сбор"})).json()["id"]

    login(reader)
    await client.post("/api/news/views", json={"post_ids": [post_id]})
    await client.post("/api/news/views", json={"post_ids": [post_id]})
    assert (await client.get("/api/news")).json()["items"][0]["views"] == 1

    login(other)
    await client.post("/api/news/views", json={"post_ids": [post_id]})
    assert (await client.get("/api/news")).json()["items"][0]["views"] == 2


async def test_leader_moderates_own_region_only(client, world, session):
    """Руководитель отделения убирает посты своих, но не чужого региона."""
    mine = await _cell_leader(session, world["moscow"].id, "МГУ", "Москвич", 2030)
    stranger = await _cell_leader(session, world["tula"].id, "ТулГУ", "Туляк", 2031)

    login(mine)
    my_post = (await client.post("/api/news", data={"text": "Пост москвича"})).json()["id"]
    login(stranger)
    other_post = (await client.post("/api/news", data={"text": "Пост туляка"})).json()["id"]

    login(world["leader_moscow"])
    feed = {i["id"]: i["can_delete"] for i in (await client.get("/api/news")).json()["items"]}
    assert feed[my_post] is True
    assert feed[other_post] is False

    assert (await client.delete(f"/api/news/{other_post}")).status_code == 403
    assert (await client.delete(f"/api/news/{my_post}")).status_code == 200


async def test_leader_cannot_delete_federal_post(client, world, session):
    """Уровень важнее географии: федеральное объявление отделению не подчинено."""
    login(world["federal"])
    federal_post = (await client.post("/api/news", data={"text": "От Братства"})).json()["id"]

    login(world["leader_moscow"])
    assert (await client.delete(f"/api/news/{federal_post}")).status_code == 403

    # А федеральный убирает что угодно.
    login(world["leader_moscow"])
    leader_post = (await client.post("/api/news", data={"text": "От отделения"})).json()["id"]
    login(world["federal"])
    assert (await client.delete(f"/api/news/{leader_post}")).status_code == 200
    assert (await client.delete(f"/api/news/{federal_post}")).status_code == 200


async def test_cell_leader_moderates_only_his_cell(client, world, session):
    """Своя ячейка — своя; соседняя в том же регионе уже не его."""
    neighbour = await _cell_leader(session, world["moscow"].id, "МГУ", "Эмгэушник", 2033)

    login(neighbour)
    other_cell_post = (await client.post("/api/news", data={"text": "Пост соседней ячейки"})).json()["id"]

    login(world["cell_leader"])
    own_post = (await client.post("/api/news", data={"text": "Пост из ячейки"})).json()["id"]
    assert (await client.delete(f"/api/news/{other_cell_post}")).status_code == 403
    assert (await client.delete(f"/api/news/{own_post}")).status_code == 200


async def test_coordinator_moderates_his_regions(client, world, session):
    """Координатор заведён только на Москву (см. фикстуру world)."""
    moscow_user = await _cell_leader(session, world["moscow"].id, "МГУ", "Москвич", 2034)
    tula_user = await _cell_leader(session, world["tula"].id, "ТулГУ", "Туляк", 2035)

    login(moscow_user)
    moscow_post = (await client.post("/api/news", data={"text": "Москва"})).json()["id"]
    login(tula_user)
    tula_post = (await client.post("/api/news", data={"text": "Тула"})).json()["id"]

    login(world["coordinator"])
    assert (await client.delete(f"/api/news/{tula_post}")).status_code == 403
    assert (await client.delete(f"/api/news/{moscow_post}")).status_code == 200


async def test_participant_moderates_nothing(client, world, session):
    """Участнику в ленте нечего удалять: своих постов у него больше нет,
    а чужие ему не подчинены."""
    author = await _cell_leader(session, world["moscow"].id, "МГУ", "Москвич", 2036)
    reader = await _participant(session, world["moscow"].id, "Другой москвич", 2037)

    login(author)
    post_id = (await client.post("/api/news", data={"text": "Своё"})).json()["id"]

    login(reader)
    feed = (await client.get("/api/news")).json()["items"]
    assert all(item["can_delete"] is False for item in feed)
    assert (await client.delete(f"/api/news/{post_id}")).status_code == 403

    login(author)
    assert (await client.delete(f"/api/news/{post_id}")).status_code == 200


async def test_profile_hides_phone_but_never_telegram(client, world, session):
    """Номер сдавали для учёта — его не видно, пока человек сам не разрешит.
    Ник в телеграме виден всегда: по нему открывается переписка, и ради этого
    профиль чаще всего и открывают."""
    author = await _participant(session, world["moscow"].id, "Волков Михаил Сергеевич", 2040)
    reader = await _participant(session, world["tula"].id, "Туляк", 2041)

    member = await session.get(Member, author.member_id)
    member.phone = "+7 900 000-00-00"
    member.telegram_username = "@volkov"
    session.add(member)
    await session.commit()

    login(reader)
    profile = (await client.get(f"/api/profile/{author.id}")).json()
    assert profile["name"] == "Волков Михаил"
    assert profile["telegram"] == "@volkov"
    # Скрытый номер не отдаём вовсе — на экране не должно быть даже строки
    # «скрыто»: скрытое не должно себя выдавать.
    assert profile["phone"] is None
    assert profile["is_me"] is False

    # Человек разрешает — появляется и номер.
    login(author)
    await client.patch("/api/profile/me", json={"show_phone": True})

    login(reader)
    profile = (await client.get(f"/api/profile/{author.id}")).json()
    assert profile["phone"] == "+7 900 000-00-00"
    assert profile["telegram"] == "@volkov"


async def test_profile_without_telegram_says_so(client, world, session):
    """Кого завели через «Состав», у того ника может не быть — профиль отдаёт
    пустое поле, и кнопка «Написать» уже не притворяется рабочей."""
    author = await _participant(session, world["moscow"].id, "Соколов Пётр", 2044)
    reader = await _participant(session, world["tula"].id, "Читатель", 2045)

    login(reader)
    assert (await client.get(f"/api/profile/{author.id}")).json()["telegram"] is None


async def test_profile_counts_reads_of_own_posts(client, world, session):
    """Счётчики в профиле — про публикации: сколько написал и сколько это
    прочитали. Они и есть повод выложить следующий пост."""
    author = await _cell_leader(session, world["moscow"].id, "МГУ", "Волков Михаил", 2046)
    reader = await _participant(session, world["tula"].id, "Читатель", 2047)

    login(author)
    post_id = (await client.post("/api/news", data={"text": "Мой пост"})).json()["id"]

    login(reader)
    await client.post("/api/news/views", json={"post_ids": [post_id]})
    await client.post(f"/api/news/{post_id}/reactions", json={"emoji": "❤️"})

    profile = (await client.get(f"/api/profile/{author.id}")).json()
    assert profile["stats"] == {"posts": 1, "views": 1, "likes": 1}
    assert profile["posts"][0]["views"] == 1
    assert profile["posts"][0]["likes"] == 1


async def test_education_rows_do_not_repeat_the_cell(client, world, session):
    """Ячейка названа по вузу и стоит в шапке профиля — второй раз вуз в
    разделе «Учёба» не повторяем."""
    from api.routers.profile import education_rows

    def labels(rows):
        return [(row["label"], row["value"]) for row in rows]

    assert labels(education_rows("СПбГУ", "СПбГУ", "Юрфак", 3, False, "bachelor")) == [
        ("Факультет", "Юрфак"), ("Курс", "3-й"), ("Уровень", "Бакалавриат"),
    ]
    # Учится не там, по чему названа ячейка — это уже не повтор, а новый факт.
    assert labels(education_rows("МГУ", "СПбГУ", "Юрфак", 3, False)) == [
        ("Вуз", "МГУ"), ("Факультет", "Юрфак"), ("Курс", "3-й"),
    ]
    assert labels(education_rows("СПбГУ", None, None, None, True)) == [
        ("Вуз", "СПбГУ"), ("Обучение", "вуз окончен"),
    ]
    assert education_rows(None, None, None, None, False) == []


async def test_profile_shows_own_posts(client, world, session):
    """Публикации в профиле остались у тех, кто публикует, — у руководителей.
    У участника их теперь не бывает: в ленту он не пишет."""
    author = await _cell_leader(session, world["moscow"].id, "МГУ", "Волков Михаил", 2042)
    other = await _cell_leader(session, world["moscow"].id, "МГТУ", "Другой", 2043)

    login(author)
    await client.post("/api/news", data={"text": "Мой пост"})
    login(other)
    await client.post("/api/news", data={"text": "Чужой пост"})

    login(other)
    profile = (await client.get(f"/api/profile/{author.id}")).json()
    assert [p["text"] for p in profile["posts"]] == ["Мой пост"]

    own = (await client.get(f"/api/profile/{other.id}")).json()
    assert own["is_me"] is True


async def test_official_posts_use_logo_and_hide_profile(client, world, session):
    """За подписью Братства или отделения нет человека: аватар — логотип,
    профиль по нему не открывается."""
    login(world["federal"])
    await client.post("/api/news", data={"text": "От Братства", "official": "true"})
    login(world["leader_moscow"])
    await client.post("/api/news", data={"text": "От отделения", "official": "true"})
    await client.post("/api/news", data={"text": "От себя", "official": "false"})

    items = {i["text"]: i for i in (await client.get("/api/news")).json()["items"]}

    assert items["От Братства"]["byline_kind"] == "bratstvo"
    assert items["От Братства"]["avatar"] == "/static/logo-bratstvo.jpg"
    assert items["От Братства"]["profile_open"] is False

    assert items["От отделения"]["byline_kind"] == "otdelenie"
    assert items["От отделения"]["avatar"] == "/static/logo-otdelenie.jpg"
    assert items["От отделения"]["profile_open"] is False

    assert items["От себя"]["byline_kind"] == "personal"
    assert items["От себя"]["profile_open"] is True


async def test_newest_post_comes_first(client, world, session):
    login(world["leader_moscow"])
    await client.post("/api/news", data={"text": "Первый"})
    await client.post("/api/news", data={"text": "Второй"})
    texts = [i["text"] for i in (await client.get("/api/news")).json()["items"]]
    assert texts[0] == "Второй"


async def test_own_avatar_is_signed_and_shows_in_profile(client, world, session):
    """Аватар живёт в профиле. В ленте его больше нет: посты выходят от
    ячейки, отделения или Братства, и на них стоит логотип, а не лицо."""
    person = await _participant(session, world["moscow"].id, "Волков Михаил", 2050)
    login(person)

    # Без загруженного фото аватара нет — клиент рисует кружок с буквой.
    assert (await client.get(f"/api/profile/{person.id}")).json()["avatar"] is None

    uploaded = await client.post(
        "/api/profile/me/avatar",
        files={"file": ("me.jpg", _tiny_jpeg(), "image/jpeg")},
    )
    assert uploaded.status_code == 200
    assert "?t=" in uploaded.json()["avatar"]

    url = (await client.get(f"/api/profile/{person.id}")).json()["avatar"]
    assert "/api/profile/avatar/" in url

    # Файл отдаётся по подписи и не отдаётся без неё.
    assert (await client.get(url)).status_code == 200
    assert (await client.get(url.split("?")[0])).status_code == 403


async def test_avatar_rejects_non_image(client, world, session):
    author = await _participant(session, world["moscow"].id, "Волков Михаил", 2051)
    login(author)
    resp = await client.post(
        "/api/profile/me/avatar",
        files={"file": ("doc.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert resp.status_code == 400


async def test_badge_only_for_brotherhood_members(client, world, session):
    """Герб рядом с именем — знак посвящения: он у члена Братства, но не у
    активиста и не у выпускника."""
    from database.models import MEMBER_STATUS_ACTIVIST, MEMBER_STATUS_ALUMNI, MEMBER_STATUS_MEMBER

    person = await _participant(session, world["moscow"].id, "Волков Михаил", 2060)
    reader = await _participant(session, world["moscow"].id, "Москвич", 2061)

    async def badge_of(status):
        member = await session.get(Member, person.member_id)
        member.status = status
        session.add(member)
        await session.commit()
        login(reader)
        return (await client.get(f"/api/profile/{person.id}")).json()["is_member"]

    assert await badge_of(MEMBER_STATUS_MEMBER) is True
    assert await badge_of(MEMBER_STATUS_ACTIVIST) is False
    assert await badge_of(MEMBER_STATUS_ALUMNI) is False


async def _attach_member(session, user, region_id, name, cell_id=None):
    """В фикстуре у руководителей нет карточки в составе, а профиль без неё
    не существует — в бою она есть у всех, кроме superuser."""
    member = Member(region_id=region_id, full_name=name, cell_id=cell_id)
    session.add(member)
    await session.flush()
    user.member_id = member.id
    session.add(user)
    await session.commit()
    return member


async def test_role_title_marks_leaders_only(client, world, session):
    """Руководство обозначается строкой, у участника её нет."""
    participant = await _participant(session, world["moscow"].id, "Москвич", 2062)
    await _attach_member(session, world["leader_moscow"], world["moscow"].id, "Иванов Иван")
    await _attach_member(session, world["cell_leader"], world["moscow"].id, "Кузнецов Иван", world["mgimo"].id)
    await _attach_member(session, world["coordinator"], world["moscow"].id, "Сидоров Семён")
    await _attach_member(session, world["federal"], world["moscow"].id, "Фёдоров Фёдор")

    login(participant)
    assert (await client.get("/api/profile/me")).json()["role_title"] is None

    expected = {
        world["leader_moscow"].id: "Руководитель отделения Москва",
        world["cell_leader"].id: "Руководитель ячейки МГИМО",
        world["coordinator"].id: "Координатор регионов",
        world["federal"].id: "Федеральный координатор",
    }
    for user_id, title in expected.items():
        assert (await client.get(f"/api/profile/{user_id}")).json()["role_title"] == title


async def test_about_is_visible_to_everyone(client, world, session):
    """«О себе» человек пишет сам, и это видят все — в отличие от контактов."""
    person = await _participant(session, world["moscow"].id, "Волков Михаил", 2063)
    reader = await _participant(session, world["tula"].id, "Туляк", 2064)

    login(person)
    await client.patch("/api/profile/me", json={"about": "Снимаю на плёнку, вожу на съезды."})
    assert (await client.get("/api/profile/me")).json()["about"] == "Снимаю на плёнку, вожу на съезды."

    login(reader)
    assert (await client.get(f"/api/profile/{person.id}")).json()["about"] == "Снимаю на плёнку, вожу на съезды."


async def test_analytics_covers_only_accessible_regions(client, world):
    login(world["coordinator"])
    data = (await client.get("/api/analytics/regions?period=all")).json()
    assert [item["region"] for item in data["items"]] == ["Москва"]

    login(world["federal"])
    data = (await client.get("/api/analytics/regions?period=all")).json()
    assert {item["region"] for item in data["items"]} == {"Москва", "Тула"}


async def test_star_purchases_are_closed(client, session, world):
    """Покупка за звёзды закрыта на время: награды выдают руководители из рук
    в руки. Отказ приходит на сам запрос, а не только прячется кнопка."""
    member = Member(region_id=world["moscow"].id, full_name="Покупатель Пётр", stars=50)
    session.add(member)
    await session.flush()
    user = User(full_name=member.full_name, role=ROLE_PARTICIPANT, member_id=member.id, telegram_id=930002)
    session.add(user)
    await session.commit()

    login(user)
    shop = (await client.get("/api/shop")).json()
    assert shop["purchases_open"] is False
    # Каталог при этом на виду — человеку важно знать, к чему он копит.
    assert any(item["id"] == "chevron" for item in shop["items"])
    assert (await client.post("/api/shop/chevron/buy")).status_code == 403


async def test_shop_purchase_badge_clears_when_leader_views(client, session, world, monkeypatch):
    """Механика покупки цела и ждёт, когда магазин откроют обратно."""
    monkeypatch.setattr("api.routers.shop.PURCHASES_OPEN", True)
    region_id = world["moscow"].id
    member = Member(region_id=region_id, full_name="Покупатель Пётр", stars=50)
    session.add(member)
    await session.flush()
    member_id = member.id
    user = User(full_name=member.full_name, role=ROLE_PARTICIPANT, member_id=member_id, telegram_id=930001)
    session.add(user)
    await session.commit()

    login(user)
    bought = await client.post("/api/shop/chevron/buy")
    assert bought.status_code == 200, bought.text

    login(world["leader_moscow"])
    before = (await client.get("/api/me")).json()["counters"]
    assert before["new_purchases"] == 1

    viewed = await client.get(f"/api/members/{member_id}/shop-purchases")
    assert viewed.status_code == 200

    after = (await client.get("/api/me")).json()["counters"]
    assert after["new_purchases"] == 0

    session.expire_all()
    purchase = (await session.execute(select(ShopPurchase).where(ShopPurchase.member_id == member_id))).scalar_one()
    assert purchase.seen_by_leader is True


async def test_reports_xlsx_and_pdf(client, world):
    login(world["leader_moscow"])
    region_id = world["moscow"].id
    await client.post("/api/members", json={"region_id": region_id, "full_name": "Отчётный Человек"})
    await client.post(
        "/api/finance/transactions",
        json={"region_id": region_id, "amount": 100000, "type": "income", "date": "2026-07-01"},
    )

    xlsx = await client.get(f"/api/reports/region?region_id={region_id}&format=xlsx&period=all")
    assert xlsx.status_code == 200
    assert xlsx.content[:2] == b"PK"  # zip-контейнер xlsx

    pdf = await client.get(f"/api/reports/region?region_id={region_id}&format=pdf&period=all")
    assert pdf.status_code == 200
    assert pdf.content[:4] == b"%PDF"


async def test_repeated_publish_does_not_multiply_the_post(client, world):
    """Двенадцать одинаковых постов за полминуты — это не двенадцать новостей,
    а двенадцать нажатий. Так и случилось в боевом кабинете: пока запрос шёл,
    кнопка оставалась живой. Кнопку починили, но полагаться только на неё
    нельзя — сорвавшаяся сеть повторит запрос сама."""
    login(world["leader_moscow"])

    ids = []
    for _ in range(5):
        response = await client.post("/api/news", data={"text": "Газ знакомиться"})
        assert response.status_code == 200, response.text
        ids.append(response.json()["id"])

    # Все пять нажатий указывают на одну и ту же новость.
    assert len(set(ids)) == 1
    assert len((await client.get("/api/news")).json()["items"]) == 1


async def test_different_texts_are_still_separate_posts(client, world):
    """Защита не должна съедать настоящие вторые новости."""
    login(world["leader_moscow"])
    await client.post("/api/news", data={"text": "Собрание в среду"})
    await client.post("/api/news", data={"text": "Съезд в декабре"})

    assert len((await client.get("/api/news")).json()["items"]) == 2


async def test_same_text_from_different_people_is_not_a_duplicate(client, world, session):
    """Совпало у двоих — это две новости, а не повтор."""
    login(world["leader_moscow"])
    await client.post("/api/news", data={"text": "С праздником!"})
    login(world["leader_tula"])
    await client.post("/api/news", data={"text": "С праздником!"})

    assert len((await client.get("/api/news")).json()["items"]) == 2
