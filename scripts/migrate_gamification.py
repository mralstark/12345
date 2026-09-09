"""Миграция: геймификация «Персонаж и инвентарь» (MVP) — таблицы
quests/member_quest_progress, плюс сид стартовых заданий. Колонка выбора
образа — members.avatar_outfit, заводится отдельно в
scripts/migrate_avatar_outfit.py (её нужно запускать после этой).

    python -m scripts.migrate_gamification
"""

import asyncio

from sqlalchemy import inspect, select

from database.db import async_session, engine
from database.models import Base, MemberQuestProgress, Quest

# (заголовок, эмодзи, лесенка порогов, цена ступеней)
# Пустая цена — ступень стоит столько же, сколько её порог (Quest.rewards_list).
SEED_QUESTS = [
    ("Сыграть в килу", "⚽", "1,3,10", ""),
    ("Посетить мероприятие", "📓", "1,3,10", ""),
    ("Сходить на службу с Академистами", "✝️", "1,3,10", ""),
    ("Написать пост в соцсети об отделении", "🪶", "1,3", ""),
    ("Помочь в организации мероприятия", "🎗️", "1,3", ""),
    ("Привести нового участника", "📖", "1", ""),
    ("Прочитать книгу", "📚", "1,3", ""),
    ("Помочь другому участнику Братства", "🤝", "1,3", ""),
    # Начинается с одного раза, как кила: человек встал в стенку впервые —
    # это уже событие, и отметить его надо сразу, а не на третий раз.
    ("Встать в стенку", "🧱", "1,3,10", ""),
    # Порог здесь — объём работы, а не заслуга, поэтому цена своя: за все
    # пятьсот стикеров выходит 21 ★, вровень с остальным каталогом, а не 925.
    ("Расклеить стикеры", "📌", "25,50,100,250,500", "1,2,3,5,10"),
    ("Выиграть чемпионат по киле", "🏆", "1"),
]


async def migrate() -> None:
    async with engine.begin() as conn:
        for table, model in (("quests", Quest), ("member_quest_progress", MemberQuestProgress)):
            has_table = await conn.run_sync(lambda sync_conn, t=table: inspect(sync_conn).has_table(t))
            if not has_table:
                await conn.run_sync(lambda sync_conn, m=model: Base.metadata.create_all(sync_conn, tables=[m.__table__]))
                print(f"Создана таблица {table}")
            else:
                print(f"Таблица {table} уже есть")

    async with async_session() as session:
        existing_titles = {q.title for q in (await session.execute(select(Quest))).scalars().all()}
        added = 0
        for position, row in enumerate(SEED_QUESTS):
            title, emoji, thresholds = row[0], row[1], row[2]
            rewards = row[3] if len(row) > 3 else ""
            if title in existing_titles:
                continue
            session.add(
                Quest(title=title, emoji=emoji, thresholds=thresholds, rewards=rewards, position=position)
            )
            added += 1
        if added:
            await session.commit()
        print(f"Добавлено заданий: {added} (всего в списке: {len(SEED_QUESTS)})")


if __name__ == "__main__":
    asyncio.run(migrate())
