"""Кому можно поставить задачу и где она потом лежит.

Постановка задачи — управленческое действие: раздаёт их руководитель, из
кабинета управления, вниз по своему отделению. Раньше здесь была ещё и
горизонталь — посвящённые в Братство ставили задачи друг другу; от неё
отказались, потому что у поручения между равными нет ответственного.

И вся работа человека лежит в одном ящике: обычные задачи вместе с задачами
мероприятий. Раньше вторые приходили в «Академии», к заданиям про килу и книги,
и мешали двум разным вещам ужиться.
"""

from database.models import (
    MEMBER_STATUS_ACTIVIST,
    MEMBER_STATUS_ALUMNI,
    MEMBER_STATUS_MEMBER,
    ROLE_PARTICIPANT,
    Member,
    User,
)
from tests.conftest import login
from utils.access import correspondents


async def _person(session, region_id, name, telegram_id, status=MEMBER_STATUS_ACTIVIST, cell_id=None):
    member = Member(region_id=region_id, full_name=name, status=status, cell_id=cell_id)
    session.add(member)
    await session.flush()
    user = User(full_name=name, role=ROLE_PARTICIPANT, member_id=member.id, telegram_id=telegram_id)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


# --- Кому руководитель может поставить задачу ---------------------------------


async def test_leader_reaches_everyone_in_his_region(session, world):
    """Всем, кто в отделении, — без оглядки на статус. Активист получает
    задачу так же, как посвящённый: это рядовая работа, а не награда."""
    activist = await _person(session, world["moscow"].id, "Актив Активов", 8001, MEMBER_STATUS_ACTIVIST)
    member = await _person(session, world["moscow"].id, "Первый Первов", 8002, MEMBER_STATUS_MEMBER)
    alumni = await _person(session, world["moscow"].id, "Выпуск Выпусков", 8003, MEMBER_STATUS_ALUMNI)

    names = [u.full_name for u in await correspondents(session, world["leader_moscow"])]

    assert activist.full_name in names
    assert member.full_name in names
    assert alumni.full_name in names


async def test_leader_keeps_the_vertical(session, world):
    """Вниз в состав — новое, вверх по должности — как было."""
    names = [u.full_name for u in await correspondents(session, world["leader_moscow"])]
    assert world["federal"].full_name in names


async def test_the_region_border_holds(session, world):
    """Между отделениями связей нет никаких — ни вверх, ни вбок."""
    stranger = await _person(session, world["tula"].id, "Тульский Тулов", 8004, MEMBER_STATUS_MEMBER)
    names = [u.full_name for u in await correspondents(session, world["leader_moscow"])]
    assert stranger.full_name not in names


async def test_ordinary_person_assigns_to_nobody(session, world):
    """Задачу он получает, а не раздаёт. Посвящение этого не меняет: раньше
    члены Братства ставили задачи друг другу, теперь — нет."""
    member = await _person(session, world["moscow"].id, "Первый Первов", 8005, MEMBER_STATUS_MEMBER)
    assert await correspondents(session, member) == []


async def test_cell_leader_stays_inside_his_cell(session, world):
    """Руководитель ячейки управляет её частью отделения, а не всем отделением."""
    inside = await _person(session, world["moscow"].id, "Свой Своев", 8006, cell_id=world["mgimo"].id)
    outside = await _person(session, world["moscow"].id, "Чужой Чужов", 8007)

    names = [u.full_name for u in await correspondents(session, world["cell_leader"])]

    assert inside.full_name in names
    assert outside.full_name not in names, "достал до человека вне своей ячейки"


# --- Через живые пути ---------------------------------------------------------


async def test_leader_assigns_to_an_activist(client, session, world):
    activist = await _person(session, world["moscow"].id, "Актив Активов", 8008)
    login(world["leader_moscow"])

    response = await client.post("/api/tasks", json={"to_user_id": activist.id, "title": "Развесить афиши"})

    assert response.status_code == 200
    assert (await client.get("/api/tasks?box=outbox")).json()["items"][0]["title"] == "Развесить афиши"


async def test_activist_cannot_assign_back(client, session, world):
    """Отказ приходит и на прямой запрос, не только кнопка спрятана."""
    activist = await _person(session, world["moscow"].id, "Актив Активов", 8009)
    login(activist)

    response = await client.post(
        "/api/tasks", json={"to_user_id": world["leader_moscow"].id, "title": "Нельзя"})

    assert response.status_code == 403


# --- Один ящик на всю работу --------------------------------------------------


async def test_event_task_lands_in_the_same_inbox(client, session, world):
    """Задача мероприятия приходит в «Задачи», а не в «Академии»: для человека
    это такая же работа, как поручение напрямую."""
    doer = await _person(session, world["moscow"].id, "Актив Активов", 8010)
    login(world["leader_moscow"])

    event = (await client.post("/api/events", json={
        "region_id": world["moscow"].id, "title": "Съезд", "date": "2026-09-14", "description": "—",
    })).json()
    await client.post(f"/api/events/{event['id']}/tasks", json={
        "title": "Договориться с площадкой", "due_date": "2026-09-01",
        "branch": "organizer", "assignee_member_id": doer.member_id,
    })

    login(doer)
    items = (await client.get("/api/tasks?box=inbox")).json()["items"]

    assert [i["title"] for i in items] == ["Договориться с площадкой"]
    assert items[0]["kind"] == "event"
    assert items[0]["from_name"] == "Съезд", "видно, от какого мероприятия задача"


async def test_event_task_is_not_in_the_lobby_anymore(client, session, world):
    """«Академия» осталась про достижения: там только каталожные задания."""
    doer = await _person(session, world["moscow"].id, "Актив Активов", 8011)
    login(world["leader_moscow"])

    event = (await client.post("/api/events", json={
        "region_id": world["moscow"].id, "title": "Съезд", "date": "2026-09-14", "description": "—",
    })).json()
    await client.post(f"/api/events/{event['id']}/tasks", json={
        "title": "Договориться с площадкой", "due_date": "2026-09-01",
        "branch": "organizer", "assignee_member_id": doer.member_id,
    })

    login(doer)
    lobby = (await client.get("/api/character/me")).json()

    assert "new_event_tasks" not in lobby
    for branch in lobby["branches"]:
        assert "event_tasks" not in branch, "задачи мероприятий всё ещё в Академии"


# --- Откуда задачи раздают -----------------------------------------------------


async def test_leader_cannot_assign_from_his_personal_cabinet(client, session, world):
    """Права никуда не деваются — меняется место. В личном кабинете «Задачи»
    почтовый ящик: работу там получают, а не раздают."""
    activist = await _person(session, world["moscow"].id, "Актив Активов", 8020)
    login(world["leader_moscow"])

    refused = await client.post(
        "/api/tasks?personal=true", json={"to_user_id": activist.id, "title": "Нельзя отсюда"})

    assert refused.status_code == 403
    assert (await client.get("/api/tasks?box=outbox")).json()["items"] == []


async def test_the_same_leader_assigns_from_the_management_cabinet(client, session, world):
    activist = await _person(session, world["moscow"].id, "Актив Активов", 8021)
    login(world["leader_moscow"])

    assert (await client.post(
        "/api/tasks", json={"to_user_id": activist.id, "title": "Развесить афиши"})).status_code == 200


async def test_the_button_is_hidden_in_the_personal_cabinet(client, session, world):
    """Кнопки быть не должно — отказ на нажатие это вторая линия."""
    await _person(session, world["moscow"].id, "Актив Активов", 8022)
    login(world["leader_moscow"])

    assert (await client.get("/api/tasks?box=inbox&personal=true")).json()["can_assign"] is False
    assert (await client.get("/api/tasks?box=inbox")).json()["can_assign"] is True
    assert (await client.get("/api/tasks/assignees?personal=true")).json()["items"] == []


# --- Исполнитель и задача мероприятия ------------------------------------------


async def _event_task_for(client, session, world, doer):
    login(world["leader_moscow"])
    event = (await client.post("/api/events", json={
        "region_id": world["moscow"].id, "title": "Съезд", "date": "2026-09-14", "description": "—",
    })).json()
    return (await client.post(f"/api/events/{event['id']}/tasks", json={
        "title": "Договориться с площадкой", "due_date": "2026-09-01",
        "assignee_member_id": doer.member_id,
    })).json()


async def test_assignee_closes_his_event_task(client, session, world):
    """Раньше отметить выполнение мог только руководитель, на самом
    мероприятии, — для человека задача выходила тупиком: пришла в ящик, а
    сделать с ней нечего."""
    doer = await _person(session, world["moscow"].id, "Актив Активов", 8030)
    task = await _event_task_for(client, session, world, doer)

    login(doer)
    response = await client.post(f"/api/tasks/event/{task['id']}/status", json={"status": "done"})

    assert response.status_code == 200
    assert (await client.get("/api/tasks?box=inbox")).json()["items"][0]["effective_status"] == "done"


async def test_assignee_can_take_it_back(client, session, world):
    """Ошибочное нажатие исправляет тот же человек, а не кто-то другой."""
    doer = await _person(session, world["moscow"].id, "Актив Активов", 8031)
    task = await _event_task_for(client, session, world, doer)

    login(doer)
    await client.post(f"/api/tasks/event/{task['id']}/status", json={"status": "done"})
    back = await client.post(f"/api/tasks/event/{task['id']}/status", json={"status": "planned"})

    assert back.status_code == 200
    assert (await client.get("/api/tasks?box=inbox")).json()["items"][0]["status"] == "planned"


async def test_stranger_cannot_close_someone_elses_event_task(client, session, world):
    doer = await _person(session, world["moscow"].id, "Актив Активов", 8032)
    stranger = await _person(session, world["moscow"].id, "Чужой Чужов", 8033)
    task = await _event_task_for(client, session, world, doer)

    login(stranger)
    refused = await client.post(f"/api/tasks/event/{task['id']}/status", json={"status": "done"})

    assert refused.status_code == 403


# --- Язык задачи --------------------------------------------------------------


async def test_event_task_speaks_the_language_of_tasks(client, session, world):
    """«Запланировано» — это про событие в календаре, а не про работу, которую
    кто-то делает. У задачи состояния свои: новая, в работе, выполнена."""
    doer = await _person(session, world["moscow"].id, "Актив Активов", 8040)
    task = await _event_task_for(client, session, world, doer)

    login(doer)
    items = (await client.get("/api/tasks?box=inbox")).json()["items"]
    assert items[0]["status_label"] == "Новая"
    assert items[0]["effective_status"] == "new"


async def test_assignee_can_take_the_event_task_into_work(client, session, world):
    """Без промежуточного состояния человек не мог сказать, что взялся, —
    только «сделано» или ничего."""
    doer = await _person(session, world["moscow"].id, "Актив Активов", 8041)
    task = await _event_task_for(client, session, world, doer)

    login(doer)
    taken = await client.post(f"/api/tasks/event/{task['id']}/status", json={"status": "in_progress"})

    assert taken.status_code == 200
    items = (await client.get("/api/tasks?box=inbox")).json()["items"]
    assert items[0]["status_label"] == "В работе"
    assert items[0]["effective_status"] == "in_progress"


# --- Красный кружок ------------------------------------------------------------


async def test_touching_an_event_task_clears_the_badge(client, session, world):
    """Кружок горел вечно: отметку о прочтении задачам мероприятий не ставил
    никто с тех пор, как они уехали из «Академии», — там её ставил экран
    категорий, а в «Задачах» ставить было некому."""
    doer = await _person(session, world["moscow"].id, "Актив Активов", 8050)
    task = await _event_task_for(client, session, world, doer)

    login(doer)
    assert (await client.get("/api/me")).json()["counters"]["new_tasks"] == 1

    await client.post(f"/api/tasks/event/{task['id']}/read")

    assert (await client.get("/api/me")).json()["counters"]["new_tasks"] == 0


async def test_finishing_an_event_task_clears_it_too(client, session, world):
    """Даже если карточку не открывали, а нажали прямо в списке."""
    doer = await _person(session, world["moscow"].id, "Актив Активов", 8051)
    task = await _event_task_for(client, session, world, doer)

    login(doer)
    await client.post(f"/api/tasks/event/{task['id']}/status", json={"status": "done"})

    assert (await client.get("/api/me")).json()["counters"]["new_tasks"] == 0


async def test_finished_task_stops_nagging(client, session, world):
    """Задачи, выполненные до того, как отметку о прочтении вообще стали
    ставить, остались бы непрочитанными навсегда — и кружок не погас бы уже
    никогда. Законченная задача не может звать к себе."""
    from database.models import EventTask, EventTaskAssignee

    doer = await _person(session, world["moscow"].id, "Актив Активов", 8060)
    task = await _event_task_for(client, session, world, doer)

    # Точно то состояние, что нашлось на боевом: выполнена и не прочитана.
    row = await session.get(EventTask, task["id"])
    row.status = "done"
    assignment = (await session.execute(
        EventTaskAssignee.__table__.select().where(EventTaskAssignee.task_id == task["id"]))).first()
    (await session.get(EventTaskAssignee, assignment.id)).is_read = False
    await session.commit()

    login(doer)
    assert (await client.get("/api/me")).json()["counters"]["new_tasks"] == 0
