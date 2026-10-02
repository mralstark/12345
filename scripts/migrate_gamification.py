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
    ("Сыграть в килу", "⚽", "1,3,10", "", "Примите участие в тренировке или игре."),
    ("Посетить мероприятие", "📓", "1,3,10", "", "Посетите мероприятие отделения."),
    ("Сходить на службу с Академистами", "✝️", "1,3,10", "", "Присоединитесь к общей службе или паломнической поездке."),
    ("Написать пост в соцсети об отделении", "🪶", "1", "", "Подготовьте живой рассказ в своём блоге или на странице ВКонтакте о людях или событии отделения."),
    ("Помочь в организации мероприятия", "🎗️", "1,3", "", "Возьмите конкретную зону ответственности и доведите её до результата."),
    ("Привести нового участника", "📖", "1,3,10", "", "Помогите ему прийти на первую встречу и подать заявку."),
    ("Выручить корпоранта", "🤝", "1,3", "", "Окажите конкретную помощь и кратко опишите результат."),
    ("Встать в стенку", "🧱", "1,3,10", "", "Пройдите тренировку в стенке и поддержите товарищей по команде."),
    ("Расклеить стикеры", "📌", "1", "", "Согласуйте места и приложите короткий отчёт."),
    ("Выиграть чемпионат по киле", "🏆", "1", "", "Станьте частью команды-победителя официального чемпионата."),
    ("Сделать фотоисторию мероприятия", "📷", "1,3", "2,5", "Соберите серию кадров с подписями, которая передаёт атмосферу события."),
    ("Выступить с короткой лекцией", "💡", "1,3", "3,7", "Подготовьте 10–15 минут содержательного выступления для отделения. Максимум — три выступления."),
    ("Подготовить анонс мероприятия", "📣", "1", "", "Подготовьте анонс мероприятия и согласуйте его с руководителем."),
    ("Написать пост в паблик регионального отделения", "✍️", "1", "", "Напишите пост в паблик регионального отделения и согласуйте публикацию."),
    ("Принять участие в балу / танцевальном вечере", "💃", "1", "", "Примите участие в балу или танцевальном вечере отделения."),
]
RETIRED_TITLES = {
    "Прочитать книгу", "Провести экскурсию по истории города", "Провести дискуссионный клуб",
    "Стать наставником новичка", "Организовать добровольческую акцию",
}
RENAMES = {"Выручить корпоранта": "Помочь другому участнику Братства"}


async def sync_catalog(session) -> None:
    existing = {q.title: q for q in (await session.execute(select(Quest))).scalars()}
    progress = list((await session.execute(select(MemberQuestProgress))).scalars())
    by_quest = {}
    for row in progress:
        by_quest.setdefault(row.quest_id, []).append(row)
    for title, q in existing.items():
        if title in RETIRED_TITLES or any(title in (r[0], RENAMES.get(r[0])) and
            (q.thresholds != r[2] or q.rewards != r[3]) for r in SEED_QUESTS):
            for row in by_quest.get(q.id, []):
                earned = sum(reward for target, reward in zip(q.thresholds_list(), q.rewards_list()) if target <= row.count)
                row.earned_stars_floor = max(row.earned_stars_floor or 0, earned, row.stars_claimed)
        if title in RETIRED_TITLES:
            q.is_active = False
    for position, (title, emoji, thresholds, rewards, description) in enumerate(SEED_QUESTS):
        q = existing.get(title) or existing.get(RENAMES.get(title))
        if q is None:
            q = Quest(title=title, emoji=emoji, thresholds=thresholds, rewards=rewards)
            session.add(q)
        q.title, q.emoji, q.thresholds, q.rewards = title, emoji, thresholds, rewards
        q.description, q.position, q.is_active = description, position, True
        for row in by_quest.get(q.id, []):
            if row.count >= max(q.thresholds_list(), default=0):
                row.assigned_by_user_id = None
                row.assignment_note = None
                row.assigned_at = None
                row.pending_count = 0
                row.submitted_note = None
                row.submitted_at = None
    await session.commit()



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
        await sync_catalog(session)
        print(f"Каталог обновлён: {len(SEED_QUESTS)} действующих заданий")


if __name__ == "__main__":
    asyncio.run(migrate())
