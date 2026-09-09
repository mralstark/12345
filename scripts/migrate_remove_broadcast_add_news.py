"""Миграция: убрать «Рассылку» (BroadcastMessage/BroadcastRecipient) и завести
вместо неё «Новости» (NewsPost/NewsPostRegion/NewsPhoto/NewsComment).

Разница по сути: рассылка адресовалась поимённо руководителям регионов и вела
учёт прочтений, а новость публикуется на аудиторию, которую задаёт роль автора
(федеральный — всем, координатор — своим регионам, руководитель отделения —
своему региону, руководитель ячейки — своей ячейке). Учёта прочтений нет.

Старые рассылки не переносятся: у них другая адресная модель (получатели —
руководители, а не участники), и осмысленно превратить их в новости нельзя.
Таблицы удаляются вместе с содержимым.

    python -m scripts.migrate_remove_broadcast_add_news
"""

import asyncio

from sqlalchemy import inspect, text

from database.db import engine
from database.models import (
    Base,
    NewsComment,
    NewsPhoto,
    NewsPost,
    NewsPostRegion,
    NewsReaction,
    NewsView,
)


async def migrate() -> None:
    async with engine.begin() as conn:
        tables = await conn.run_sync(lambda sync_conn: set(inspect(sync_conn).get_table_names()))

        # Сначала таблица-получатели: у неё внешний ключ на broadcast_messages.
        for name in ("broadcast_recipients", "broadcast_messages"):
            if name in tables:
                await conn.execute(text(f"DROP TABLE {name}"))
                print(f"Таблица {name} удалена")
            else:
                print(f"Таблицы {name} нет — пропускаю")

        created = []
        for table in (
            NewsPost.__table__,
            NewsPostRegion.__table__,
            NewsPhoto.__table__,
            NewsComment.__table__,
            NewsReaction.__table__,
            NewsView.__table__,
        ):
            if table.name not in tables:
                created.append(table)
        if created:
            await conn.run_sync(Base.metadata.create_all, tables=created)
            print("Созданы таблицы: " + ", ".join(t.name for t in created))
        else:
            print("Таблицы новостей уже на месте — создавать нечего")


if __name__ == "__main__":
    asyncio.run(migrate())
