"""Миграция: звёзды за задачу мероприятия теперь забираются кнопкой
«Получить» (EventTaskAssignee.stars_claimed), а не начисляются автоматически
при закрытии задачи — старая EventTask.stars_awarded больше не нужна.

    python -m scripts.migrate_event_task_stars_claimed
"""

import asyncio

from sqlalchemy import inspect, text

from database.db import engine


async def migrate() -> None:
    async with engine.begin() as conn:
        has_assignees = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).has_table("event_task_assignees")
        )
        if has_assignees:
            existing = await conn.run_sync(
                lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("event_task_assignees")}
            )
            if "stars_claimed" not in existing:
                await conn.execute(
                    text("ALTER TABLE event_task_assignees ADD COLUMN stars_claimed BOOLEAN DEFAULT false")
                )
                print("Добавлена колонка event_task_assignees.stars_claimed")
            else:
                print("Колонка event_task_assignees.stars_claimed уже на месте")
        else:
            print("Таблица event_task_assignees ещё не создана — миграция не нужна.")

        has_tasks = await conn.run_sync(lambda sync_conn: inspect(sync_conn).has_table("event_tasks"))
        if has_tasks:
            existing = await conn.run_sync(
                lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("event_tasks")}
            )
            if "stars_awarded" in existing:
                await conn.execute(text("ALTER TABLE event_tasks DROP COLUMN stars_awarded"))
                print("Колонка event_tasks.stars_awarded удалена")
            else:
                print("Колонка event_tasks.stars_awarded уже отсутствует")
        else:
            print("Таблица event_tasks ещё не создана — миграция не нужна.")


if __name__ == "__main__":
    asyncio.run(migrate())
