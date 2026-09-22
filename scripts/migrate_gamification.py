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
    ("Сыграть в килу", "⚽", "1,3,10", "", "Примите участие в тренировке или игре и расскажите, за какую команду играли."),
    ("Посетить мероприятие", "📓", "1,3,10", "", "Посетите встречу отделения и отметьте главную мысль, которую унесли с собой."),
    ("Сходить на службу с Академистами", "✝️", "1,3,10", "", "Присоединитесь к общей службе или паломнической поездке."),
    ("Написать пост в соцсети об отделении", "🪶", "1,3", "", "Подготовьте живой рассказ о людях или событии отделения."),
    ("Помочь в организации мероприятия", "🎗️", "1,3", "", "Возьмите конкретную зону ответственности и доведите её до результата."),
    ("Привести нового участника", "📖", "1", "", "Познакомьте человека с Братством и помогите ему прийти на первую встречу."),
    ("Прочитать книгу", "📚", "1,3", "", "Выберите книгу из рекомендованного списка и поделитесь коротким выводом."),
    ("Помочь другому участнику Братства", "🤝", "1,3", "", "Окажите конкретную помощь и кратко опишите результат."),
    # Начинается с одного раза, как кила: человек встал в стенку впервые —
    # это уже событие, и отметить его надо сразу, а не на третий раз.
    ("Встать в стенку", "🧱", "1,3,10", "", "Пройдите тренировку в стенке и поддержите товарищей по команде."),
    # Порог здесь — объём работы, а не заслуга, поэтому цена своя: за все
    # пятьсот стикеров выходит 21 ★, вровень с остальным каталогом, а не 925.
    ("Расклеить стикеры", "📌", "25,50,100,250,500", "1,2,3,5,10", "Согласуйте места, проведите акцию и приложите короткий отчёт."),
    ("Выиграть чемпионат по киле", "🏆", "1", "", "Станьте частью команды-победителя официального чемпионата."),
    ("Провести дискуссионный клуб", "🎙️", "1,3", "2,5", "Выберите тему, подготовьте вопросы и проведите содержательную дискуссию."),
    ("Стать наставником новичка", "🧭", "1,3", "3,7", "Помогите новичку освоиться, познакомьтесь лично и сопроводите первые шаги."),
    ("Организовать добровольческую акцию", "❤️", "1,3", "3,8", "Соберите команду для полезного дела и зафиксируйте результат."),
    ("Провести экскурсию по истории города", "🏛️", "1,3", "3,7", "Подготовьте маршрут, связанный с историей России и вашего города."),
    ("Сделать фотоисторию мероприятия", "📷", "1,3", "2,5", "Соберите серию кадров с подписями, которая передаёт атмосферу события."),
    ("Выступить с короткой лекцией", "💡", "1,3", "3,7", "Подготовьте 10–15 минут содержательного выступления для отделения."),
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
            description = row[4] if len(row) > 4 else None
            existing = (await session.execute(select(Quest).where(Quest.title == title))).scalar_one_or_none()
            if existing is not None:
                existing.description = description
                existing.position = position
            else:
                session.add(Quest(title=title, emoji=emoji, thresholds=thresholds, rewards=rewards,
                                  description=description, position=position))
                added += 1
        await session.commit()
        print(f"Добавлено заданий: {added} (всего в списке: {len(SEED_QUESTS)})")


if __name__ == "__main__":
    asyncio.run(migrate())
