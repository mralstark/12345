"""Миграция: своя цена у ступени задания + правка каталога.

Три вещи разом, потому что они об одном — о том, как считается награда.

1. Колонка quests.rewards. Раньше ступень стоила столько же, сколько её
   порог: лесенка «1,3,10» платила 1, 3 и 10. Для килы это верно, а для
   «расклеить 500 стикеров» — нет: порог там меряет объём работы, а не
   заслугу, и по прежнему правилу одно это задание выдало бы 925 ★ при 73 ★
   во всём остальном каталоге.

2. «Встать в стенку» начинается с одного раза, как кила, а не с трёх.
   Человек встал в стенку впервые — это уже событие, и отметить его надо
   сразу.

3. Появляется «Расклеить стикеры»: пороги 25/50/100/250/500, цена
   1/2/3/5/10 — за все пятьсот выходит 21 ★.

    python -m scripts.migrate_quest_rewards
"""

import asyncio

from sqlalchemy import inspect, select, text

from database.db import async_session, engine
from database.models import Quest

WALL = "Встать в стенку"
STICKERS = "Расклеить стикеры"


async def migrate() -> None:
    async with engine.begin() as conn:
        columns = await conn.run_sync(lambda c: {col["name"] for col in inspect(c).get_columns("quests")})
        if "rewards" not in columns:
            await conn.execute(text("ALTER TABLE quests ADD COLUMN rewards VARCHAR(64) DEFAULT ''"))
            print("Добавлена колонка quests.rewards")
        else:
            print("Колонка quests.rewards уже есть")

    async with async_session() as session:
        quests = {q.title: q for q in (await session.execute(select(Quest))).scalars().all()}

        wall = quests.get(WALL)
        if wall is None:
            print(f"«{WALL}» в каталоге нет — пропускаю")
        elif wall.thresholds == "1,3,10":
            print(f"«{WALL}» уже начинается с одного раза")
        else:
            print(f"«{WALL}»: лесенка {wall.thresholds} → 1,3,10")
            wall.thresholds = "1,3,10"

        if STICKERS in quests:
            print(f"«{STICKERS}» уже есть")
        else:
            position = max((q.position for q in quests.values()), default=0) + 1
            session.add(
                Quest(
                    title=STICKERS,
                    emoji="📌",
                    thresholds="25,50,100,250,500",
                    rewards="1,2,3,5,10",
                    position=position,
                )
            )
            print(f"Добавлено задание «{STICKERS}» (25/50/100/250/500, цена 1/2/3/5/10)")

        await session.commit()

    async with async_session() as session:
        total = 0
        for quest in (await session.execute(select(Quest).order_by(Quest.position))).scalars().all():
            earned = sum(quest.rewards_list())
            total += earned
            print(f"   {quest.title:38} {quest.thresholds:22} → {earned} ★")
        print(f"Потолок каталога за всю жизнь: {total} ★")


if __name__ == "__main__":
    asyncio.run(migrate())
