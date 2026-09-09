"""Миграция: категория (branch) и звёзды у задач мероприятия (EventTask),
метка прочтения у EventTaskAssignee — для интеграции с Академией/Заданиями
(см. database/models.py::EventTask, api/routers/character.py::BRANCHES).

Существующие подзадачи считаются уже «прочитанными» (is_read=1) — иначе все
старые назначения разом обрушили бы красные кружки в Академии у всех сразу.

    python -m scripts.migrate_event_task_branch
"""

import asyncio

from sqlalchemy import inspect, text

from database.db import engine


async def migrate() -> None:
    async with engine.begin() as conn:
        has_tasks = await conn.run_sync(lambda sync_conn: inspect(sync_conn).has_table("event_tasks"))
        if has_tasks:
            existing = await conn.run_sync(
                lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("event_tasks")}
            )
            if "branch" not in existing:
                await conn.execute(text("ALTER TABLE event_tasks ADD COLUMN branch VARCHAR(32) DEFAULT 'organizer'"))
                print("Добавлена колонка event_tasks.branch")
            else:
                print("Колонка event_tasks.branch уже на месте")
            if "stars" not in existing:
                await conn.execute(text("ALTER TABLE event_tasks ADD COLUMN stars INTEGER DEFAULT 1"))
                print("Добавлена колонка event_tasks.stars")
            else:
                print("Колонка event_tasks.stars уже на месте")
            if "stars_awarded" not in existing:
                await conn.execute(text("ALTER TABLE event_tasks ADD COLUMN stars_awarded BOOLEAN DEFAULT false"))
                print("Добавлена колонка event_tasks.stars_awarded")
            else:
                print("Колонка event_tasks.stars_awarded уже на месте")
        else:
            print("Таблица event_tasks ещё не создана — миграция не нужна.")

        has_assignees = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).has_table("event_task_assignees")
        )
        if has_assignees:
            existing = await conn.run_sync(
                lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("event_task_assignees")}
            )
            if "is_read" not in existing:
                await conn.execute(text("ALTER TABLE event_task_assignees ADD COLUMN is_read BOOLEAN DEFAULT false"))
                await conn.execute(text("UPDATE event_task_assignees SET is_read = true"))
                print("Добавлена колонка event_task_assignees.is_read (старые назначения помечены прочитанными)")
            else:
                print("Колонка event_task_assignees.is_read уже на месте")
        else:
            print("Таблица event_task_assignees ещё не создана — миграция не нужна.")


if __name__ == "__main__":
    asyncio.run(migrate())
