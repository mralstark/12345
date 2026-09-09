"""Точечная правка данных: «Выиграть чемпионат по киле» изначально завели с
лесенкой «5» (проигрыш логики «порог = награда в звёздах»), но это разовое
достижение — правильная лесенка «1», а награда за него не звёзды, а образ
«Атаман» (см. api/routers/character.py::_OUTFIT_BY_QUEST_TITLE).

    python -m scripts.migrate_kila_championship_fix
"""

import asyncio

from sqlalchemy import select

from database.db import async_session
from database.models import Quest

TITLE = "Выиграть чемпионат по киле"
FIXED_THRESHOLDS = "1"


async def migrate() -> None:
    async with async_session() as session:
        quest = (await session.execute(select(Quest).where(Quest.title == TITLE))).scalar_one_or_none()
        if quest is None:
            print(f"Задание «{TITLE}» не найдено — нечего исправлять")
            return
        if quest.thresholds == FIXED_THRESHOLDS:
            print(f"Лесенка у «{TITLE}» уже «{FIXED_THRESHOLDS}»")
            return
        quest.thresholds = FIXED_THRESHOLDS
        await session.commit()
        print(f"Лесенка «{TITLE}» исправлена на «{FIXED_THRESHOLDS}»")


if __name__ == "__main__":
    asyncio.run(migrate())
