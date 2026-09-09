"""Миграция: подзадачи мероприятий (event_tasks, event_task_assignees) —
цель-мероприятие с несколькими подзадачами и исполнителями, см.
database/models.py::EventTask.

    python -m scripts.migrate_event_tasks
"""

import asyncio

from sqlalchemy import inspect

from database.db import engine
from database.models import Base, EventTask, EventTaskAssignee


async def migrate() -> None:
    async with engine.begin() as conn:
        for table, model in (("event_tasks", EventTask), ("event_task_assignees", EventTaskAssignee)):
            has_table = await conn.run_sync(lambda sync_conn, t=table: inspect(sync_conn).has_table(t))
            if not has_table:
                await conn.run_sync(lambda sync_conn, m=model: Base.metadata.create_all(sync_conn, tables=[m.__table__]))
                print(f"Создана таблица {table}")
            else:
                print(f"Таблица {table} уже есть")


if __name__ == "__main__":
    asyncio.run(migrate())
