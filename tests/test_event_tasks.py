"""Задачи мероприятия (раньше «подзадачи»): несколько исполнителей на задачу,
категория, срок. Выполнение задач не влияет на статус самого мероприятия.

Награды за такую задачу нет: это рядовая работа, а не достижение. Раньше
руководитель назначал её сам, 1–3 звезды, — и это был единственный канал, где
число бралось из головы. Тест ниже сторожит, что канал закрыт."""

from database.models import ROLE_PARTICIPANT, Member, User
from tests.conftest import login


async def _make_event(client, region_id, title="Конференция в СФУ"):
    created = await client.post(
        "/api/events",
        json={"region_id": region_id, "title": title, "date": "2026-08-14", "description": "Описание"},
    )
    assert created.status_code == 200, created.text
    return created.json()


def _task_payload(**overrides):
    payload = {"title": "Задача", "due_date": "2026-08-05"}
    payload.update(overrides)
    return payload


async def test_event_task_crud(client, world):
    login(world["leader_moscow"])
    region_id = world["moscow"].id

    rodchenko = (await client.post(
        "/api/members", json={"region_id": region_id, "full_name": "Родченко Д. С."}
    )).json()
    fedyulin = (await client.post(
        "/api/members", json={"region_id": region_id, "full_name": "Федюлин Р. С."}
    )).json()

    event = await _make_event(client, region_id)

    task1 = (await client.post(
        f"/api/events/{event['id']}/tasks",
        json=_task_payload(title="Договориться с СФУ", due_date="2026-08-01", assignee_member_id=rodchenko["id"]),
    )).json()
    task2 = (await client.post(
        f"/api/events/{event['id']}/tasks",
        json=_task_payload(
            title="Провести конференцию",
            due_date="2026-08-14",
            assignee_member_id=fedyulin["id"],
        ),
    )).json()
    # Исполнитель один, он же ответственный: имя приходит вместе с ним, чтобы
    # список задач не пришлось дополнять вторым запросом на каждой строке.
    assert task2["assignee_member_id"] == fedyulin["id"]
    assert task2["assignee_name"] == "Федюлин Р. С."

    # Передача другому: прежний исполнитель уходит, новый встаёт на его место.
    handed = (await client.patch(
        f"/api/events/{event['id']}/tasks/{task2['id']}",
        json={"assignee_member_id": rodchenko["id"]},
    )).json()
    assert handed["assignee_member_id"] == rodchenko["id"]

    listed = (await client.get(f"/api/events/{event['id']}/tasks")).json()["items"]
    assert [t["title"] for t in listed] == ["Договориться с СФУ", "Провести конференцию"]

    await client.patch(f"/api/events/{event['id']}/tasks/{task1['id']}", json={"status": "done"})
    still_open = (await client.get(f"/api/events/{event['id']}")).json()
    assert still_open["status"] == "planned"

    # Закрытие последней задачи не закрывает мероприятие само по себе —
    # это отдельные, независимые действия руководителя.
    await client.patch(f"/api/events/{event['id']}/tasks/{task2['id']}", json={"status": "done"})
    still_open2 = (await client.get(f"/api/events/{event['id']}")).json()
    assert still_open2["status"] == "planned"

    closed = await client.patch(f"/api/events/{event['id']}", json={"status": "done"})
    assert closed.status_code == 200
    closed_card = (await client.get(f"/api/events/{event['id']}")).json()
    tasks_by_id = {t["id"]: t["assignee_member_id"] for t in closed_card["tasks"]}
    assert tasks_by_id[task2["id"]] == rodchenko["id"]


async def test_event_task_assignee_must_be_same_region(client, world):
    login(world["leader_tula"])
    outsider = (await client.post(
        "/api/members", json={"region_id": world["tula"].id, "full_name": "Чужой Регион"}
    )).json()

    login(world["leader_moscow"])
    event = await _make_event(client, world["moscow"].id)

    rejected = await client.post(
        f"/api/events/{event['id']}/tasks",
        json=_task_payload(assignee_member_id=outsider["id"]),
    )
    assert rejected.status_code == 400


async def test_event_task_delete(client, world):
    login(world["leader_moscow"])
    event = await _make_event(client, world["moscow"].id)

    task = (await client.post(
        f"/api/events/{event['id']}/tasks", json=_task_payload(title="Единственная")
    )).json()

    deleted = await client.delete(f"/api/events/{event['id']}/tasks/{task['id']}")
    assert deleted.status_code == 200

    remaining = (await client.get(f"/api/events/{event['id']}/tasks")).json()["items"]
    assert remaining == []


async def test_event_task_ignores_a_category(client, world):
    """Категории у задачи больше нет: она заводилась ради радара в «Академии», а
    задачи мероприятий оттуда ушли. Присланную не принимаем молча — просто не
    отдаём наружу, чтобы старый клиент не сломался на отказе."""
    login(world["leader_moscow"])
    event = await _make_event(client, world["moscow"].id)

    created = await client.post(
        f"/api/events/{event['id']}/tasks", json=_task_payload(branch="nonexistent")
    )

    assert created.status_code == 200
    assert "branch" not in created.json()

async def test_event_task_gives_no_stars(client, session, world):
    """Звёзды за задачу мероприятия больше не начисляются никак. Раньше их
    назначал руководитель — от одной до трёх на задачу; девять таких задач за
    вечер намолотили тринадцать звёзд при пожизненном потолке в «Академии»
    порядка шестидесяти. Рядовую работу отмечают сделанной, а не оплачивают."""
    login(world["leader_moscow"])
    region_id = world["moscow"].id
    event = await _make_event(client, region_id)

    member = Member(region_id=region_id, full_name="Звёздный Иван")
    session.add(member)
    await session.flush()
    member_id = member.id
    user = User(full_name=member.full_name, role=ROLE_PARTICIPANT, member_id=member_id, telegram_id=920001)
    session.add(user)
    await session.commit()

    # Награду не принимают, даже если её прислать руками.
    task = (await client.post(
        f"/api/events/{event['id']}/tasks",
        json=_task_payload(stars=3, assignee_member_id=member_id),
    )).json()
    assert "stars" not in task, "награда всё ещё отдаётся наружу"

    login(world["leader_moscow"])
    await client.patch(f"/api/events/{event['id']}/tasks/{task['id']}", json={"status": "done"})

    session.expire_all()
    assert (await session.get(Member, member_id)).stars == 0, "выполненная задача принесла звёзды"
