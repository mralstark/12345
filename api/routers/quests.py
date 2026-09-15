"""Задания геймификации — сторона руководителя (план «Персонаж и инвентарь»,
MVP): просмотр прогресса конкретного человека из «Состава» и отметка
выполнения («+1» к счётчику, не галочка — см. database.models.Quest).
Список самих заданий не редактируется в веб-интерфейсе на этом этапе —
заводится один раз scripts/migrate_gamification.py."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user, get_db
from api.routers.character import _quest_dict
from database.models import Member, MemberQuestProgress, Quest, User
from utils.access import actor_cell, require_edit, require_same_cell, require_view
from utils.notify import escape_telegram_html, notify_telegram
from utils.tz import now as tz_now

router = APIRouter(prefix="/members/{member_id}/quests", tags=["quests"])


async def _get_member(session: AsyncSession, member_id: int) -> Member:
    member = await session.get(Member, member_id)
    if member is None:
        raise HTTPException(404, "Человек не найден")
    return member


@router.get("")
async def list_member_quests(
    member_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    member = await _get_member(session, member_id)
    await require_view(session, user, member.region_id)
    require_same_cell(await actor_cell(session, user), member.cell_id)

    quests = list(
        (await session.execute(select(Quest).where(Quest.is_active.is_(True)).order_by(Quest.position))).scalars().all()
    )
    progress = (
        await session.execute(select(MemberQuestProgress).where(MemberQuestProgress.member_id == member_id))
    ).scalars().all()
    by_quest = {row.quest_id: row for row in progress}
    items = [
        _quest_dict(q, (by_quest[q.id].count if q.id in by_quest else 0),
                    (by_quest[q.id].stars_claimed if q.id in by_quest else 0))
        for q in quests
    ]
    # Баланс человека — руководителю он был не виден нигде: он жал «+1» и не
    # знал ни сколько у человека звёзд, ни сколько ему открылось.
    return {
        "items": items,
        "claimed": sum(i["stars_claimed"] for i in items),
        "pending": sum(i["claimable_stars"] for i in items),
    }


async def _step(session: AsyncSession, user: User, member_id: int, quest_id: int, delta: int) -> tuple:
    """Общая часть отметки и её отмены: проверки прав и поиск строки прогресса."""
    member = await _get_member(session, member_id)
    await require_edit(session, user, member.region_id)
    require_same_cell(await actor_cell(session, user), member.cell_id)

    quest = await session.get(Quest, quest_id)
    if quest is None or not quest.is_active:
        raise HTTPException(404, "Задание не найдено")

    row = (
        await session.execute(
            select(MemberQuestProgress).where(
                MemberQuestProgress.member_id == member_id, MemberQuestProgress.quest_id == quest_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = MemberQuestProgress(member_id=member_id, quest_id=quest_id, count=0)
        session.add(row)

    before = row.count
    # Ниже нуля не уходим: отменять нечего, а отрицательный счётчик сломал бы
    # и полоску прогресса, и подсчёт звёзд.
    row.count = max(0, row.count + delta)
    row.updated_at = tz_now().replace(tzinfo=None)
    await session.commit()
    return quest, row, before


@router.post("/{quest_id}/increment")
async def increment_member_quest(
    member_id: int,
    quest_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    quest, row, before = await _step(session, user, member_id, quest_id, 1)
    thresholds = quest.thresholds_list()

    # Уведомляем только на реальном переходе через ступень лесенки, не на
    # каждом «+1» — иначе между 3 и 10 у повторяемого задания было бы 7
    # сообщений подряд без новой информации. Звёзды не начисляем здесь —
    # их сам человек забирает кнопкой «Получить» в «Академии» (claim_quest_stars).
    crossed = next((t for t in thresholds if before < t <= row.count), None)
    if crossed is not None:
        owner = (await session.execute(select(User).where(User.member_id == member_id))).scalar_one_or_none()
        if owner is not None:
            label = "Выполнено" if crossed == thresholds[-1] else f"новый уровень {crossed}"
            await notify_telegram(
                owner.telegram_id,
                f"🎉 Задание «{escape_telegram_html(quest.title)}» — "
                f"{escape_telegram_html(label)}! Заберите звёзды в «Академии».",
            )

    return _quest_dict(quest, row.count, row.stars_claimed)


@router.post("/{quest_id}/decrement")
async def decrement_member_quest(
    member_id: int,
    quest_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Снять ошибочную отметку. Промахнуться по «Отметить» легко, а отменить
    было нечем — единственным выходом оставалось лезть в базу.

    Уведомления тут нет намеренно: человеку незачем узнавать, что у него
    отняли уровень, — это исправление руководителя, а не событие. Забранные
    звёзды тоже не отбираем: что получено, то получено (см. claimable_stars,
    он не уходит ниже нуля).
    """
    quest, row, _ = await _step(session, user, member_id, quest_id, -1)
    return _quest_dict(quest, row.count, row.stars_claimed)
