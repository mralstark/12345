"""«Задачи» в Mini App (ТЗ §10.2): вкладки входящих и исходящих, смена статуса.

Во входящих лежит вся работа человека — и то, что поручили напрямую, и задачи
мероприятий. Раньше вторые приходили в «Академии», рядом с заданиями про килу и
книги, и две разные вещи мешали друг другу: задание — про то, кем человек
становится, с наградой по общей лесенке; задача — рядовая работа от
руководителя. Стоя рядом, они делали лесенку похожей на договорённость."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user, get_db
from api.serializers import task_dict, user_brief
from database.models import (
    EVENT_STATUS_CANCELLED,
    EVENT_STATUS_DONE,
    EVENT_STATUS_PLANNED,
    EVENT_TASK_STATUS_LABELS,
    TASK_STATUS_LABELS,
    Event,
    EventTask,
    EventTaskAssignee,
    Task,
    User,
)
from services.tasks import change_status, create_task, delete_task, mark_read
from utils.access import AccessDenied, correspondents

router = APIRouter(prefix="/tasks", tags=["tasks"])


class TaskIn(BaseModel):
    to_user_id: int
    title: str = Field(min_length=2, max_length=255)
    # Карточка целиком должна помещаться в одно сообщение Telegram вместе с
    # заголовком, исполнителем и кнопками.
    text: str | None = Field(default=None, max_length=3000)
    deadline: date | None = None


class StatusIn(BaseModel):
    status: str = Field(max_length=16)


@router.get("/assignees")
async def assignees(
    personal: bool = False,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    if personal:
        return {"items": []}
    return {"items": [await user_brief(session, person) for person in await correspondents(session, user)]}


async def _event_task_items(session: AsyncSession, member_id: int) -> list[dict]:
    """Задачи мероприятий этого человека — в том же ящике, что и обычные.

    Раньше они приходили в «Академии», к заданиям про килу и книги, и мешали
    двум разным вещам ужиться: задание — про то, кем человек становится, и
    награда за него взята из общей лесенки; задача — рядовая работа, которую
    поручил руководитель. Рядом они делали лесенку похожей на договорённость.
    Теперь вся работа лежит в «Задачах», а «Академия» осталась про достижения.
    """
    rows = (
        await session.execute(
            select(EventTask, Event.title, EventTaskAssignee.is_read)
            .join(EventTaskAssignee, EventTaskAssignee.task_id == EventTask.id)
            .join(Event, Event.id == EventTask.event_id)
            .where(
                EventTaskAssignee.member_id == member_id,
                EventTask.status != EVENT_STATUS_CANCELLED,
            )
            .order_by(EventTask.due_date.desc())
            .limit(200)
        )
    ).all()

    items = []
    for task, event_title, is_read in rows:
        done = task.status == EVENT_STATUS_DONE
        items.append({
            "id": "event-" + str(task.id),
            "kind": "event",
            "title": task.title,
            "text": None,
            "deadline": task.due_date.isoformat(),
            "status": task.status,
            "effective_status": task.status if task.status in ("done", "in_progress") else "new",
            "status_label": EVENT_TASK_STATUS_LABELS.get(task.status, task.status),
            "is_read": is_read,
            # Исполнитель у задачи один, он же ответственный, — статус ставит
            # он сам, отсюда же (api/routers/event_tasks.py::update_event_task).
            "can_report": not done,
            "from_name": event_title,
            "to_name": None,
            "created_at": None,
            "event_id": task.event_id,
        })
    return items


@router.get("")
async def list_tasks(
    box: str = "inbox",
    # Из какого кабинета смотрят. В личном «Задачи» — почтовый ящик: человек
    # там получает работу, а не раздаёт. Раздают из кабинета управления, куда
    # заходят именно за этим.
    personal: bool = False,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    column = Task.to_user_id if box == "inbox" else Task.from_user_id
    result = await session.execute(
        select(Task).where(column == user.id).order_by(Task.created_at.desc(), Task.id.desc()).limit(200)
    )
    tasks = list(result.scalars().all())

    people_ids = {t.from_user_id for t in tasks} | {t.to_user_id for t in tasks}
    people: dict[int, User] = {}
    if people_ids:
        people_result = await session.execute(select(User).where(User.id.in_(people_ids)))
        people = {u.id: u for u in people_result.scalars().all()}

    items = [task_dict(t, people.get(t.from_user_id), people.get(t.to_user_id)) for t in tasks]
    for item in items:
        item["kind"] = "task"
    # Поставленные мной — только обычные: задачи мероприятия живут на своём
    # мероприятии, и оттуда ими и управляют.
    if box == "inbox" and user.member_id is not None:
        items += await _event_task_items(session, user.member_id)

    return {
        "items": items,
        "statuses": [{"value": k, "label": v} for k, v in TASK_STATUS_LABELS.items()],
        # Кнопку «Поставить задачу» показываем только тому, кому есть кому её
        # поставить, и только в кабинете управления.
        "can_assign": not personal and bool(await correspondents(session, user)),
    }


@router.post("")
async def add_task(
    payload: TaskIn,
    personal: bool = False,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    # Отказ приходит и на прямой запрос, не только кнопка спрятана: из личного
    # кабинета задачи не ставят, даже если права на это есть.
    if personal:
        raise AccessDenied("Задачи ставят из кабинета управления")
    task = await create_task(session, user, payload.to_user_id, payload.title, payload.text, payload.deadline)
    assignee = await session.get(User, payload.to_user_id)
    return task_dict(task, author=user, assignee=assignee)


class EventTaskStatusIn(BaseModel):
    status: str


@router.post("/event/{task_id}/read")
async def mark_event_task_read(
    task_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Задача открыта — кружок гаснет. То же самое, что у обычной задачи
    (services/tasks.py::mark_read), только строка о прочтении лежит рядом с
    назначением, а не в самой задаче: у неё свой исполнитель."""
    if user.member_id is None:
        return {"ok": True}
    row = (
        await session.execute(
            select(EventTaskAssignee).where(
                EventTaskAssignee.task_id == task_id, EventTaskAssignee.member_id == user.member_id
            )
        )
    ).scalar_one_or_none()
    if row is not None and not row.is_read:
        row.is_read = True
        await session.commit()
    return {"ok": True}


@router.post("/event/{task_id}/status")
async def set_event_task_status(
    task_id: int,
    payload: EventTaskStatusIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Исполнитель отмечает задачу мероприятия выполненной — и может вернуть
    обратно, если поторопился.

    Раньше это мог только руководитель, на самом мероприятии. Для человека
    задача выходила тупиком: пришла в ящик, а сделать с ней нечего. Отмена
    оставлена намеренно — без неё ошибочное нажатие исправлял бы кто-то
    другой, и человек снова оказывался бы ни при чём.

    Статус у задачи один на всех исполнителей: это одна работа, а не своя у
    каждого, — так же, как когда её закрывает руководитель.
    """
    if payload.status not in (EVENT_STATUS_PLANNED, "in_progress", EVENT_STATUS_DONE):
        raise HTTPException(400, f"Неизвестный статус: {payload.status}")
    if user.member_id is None:
        raise AccessDenied("Задача не ваша")

    task = await session.get(EventTask, task_id)
    if task is None:
        raise HTTPException(404, "Задача не найдена")

    assigned = (
        await session.execute(
            select(EventTaskAssignee).where(
                EventTaskAssignee.task_id == task_id, EventTaskAssignee.member_id == user.member_id
            )
        )
    ).scalar_one_or_none()
    if assigned is None:
        raise AccessDenied("Задача не ваша")

    task.status = payload.status
    # Раз человек её тронул — она точно прочитана. Отметки об этом у задач
    # мероприятий не проставлял никто с тех пор, как они уехали из «Академии»:
    # там её ставил экран категорий, а в «Задачах» ставить было некому. Из-за
    # этого красный кружок горел вечно — даже когда всё уже выполнено.
    assigned.is_read = True
    await session.commit()
    return {"ok": True, "status": task.status}


@router.post("/{task_id}/status")
async def set_status(
    task_id: int,
    payload: StatusIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    task = await session.get(Task, task_id)
    if task is None:
        raise HTTPException(404, "Задача не найдена")
    task = await change_status(session, task, user, payload.status)
    author = await session.get(User, task.from_user_id)
    assignee = await session.get(User, task.to_user_id)
    return task_dict(task, author=author, assignee=assignee)


@router.delete("/{task_id}")
async def remove_task(
    task_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Снять задачу. Отменённых задач у нас нет — снятая исчезает совсем
    (services/tasks.py::delete_task)."""
    task = await session.get(Task, task_id)
    if task is None:
        raise HTTPException(404, "Задача не найдена")
    await delete_task(session, task, user)
    return {"ok": True}


@router.post("/{task_id}/read")
async def read_task(
    task_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    task = await session.get(Task, task_id)
    if task is None:
        raise HTTPException(404, "Задача не найдена")
    await mark_read(session, task, user.id)
    return {"ok": True}
