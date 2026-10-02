"""Задания геймификации — сторона руководителя (план «Персонаж и инвентарь»,
MVP): просмотр прогресса конкретного человека из «Состава» и отметка
выполнения («+1» к счётчику, не галочка — см. database.models.Quest).
Список самих заданий не редактируется в веб-интерфейсе на этом этапе —
заводится один раз scripts/migrate_gamification.py."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user, get_db
from api.routers.character import _quest_dict
from database.models import ROLE_SUPERUSER, Member, MemberQuestProgress, Quest, QuestActivity, User
from services.academy import clear_assignment, lock_progress, log_activity, require_academy, require_student
from utils.access import AccessDenied, actor_cell, require_same_cell
from utils.notify import escape_telegram_html, notify_telegram
from utils.tz import now as tz_now
from utils.permissions import has_role

router = APIRouter(prefix="/members/{member_id}/quests", tags=["quests"])


class QuestAssignmentIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


async def _get_member(session: AsyncSession, member_id: int) -> Member:
    member = (await session.execute(select(Member).where(Member.id == member_id).with_for_update())).scalar_one_or_none()
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
    await require_academy(session, user, member)
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
                    (by_quest[q.id].stars_claimed if q.id in by_quest else 0),
                    (by_quest[q.id].pending_count if q.id in by_quest else 0),
                    (by_quest[q.id].submitted_note if q.id in by_quest else None),
                    (by_quest[q.id].assigned_by_user_id if q.id in by_quest else None),
                    (by_quest[q.id].assignment_note if q.id in by_quest else None),
                    (by_quest[q.id].earned_stars_floor if q.id in by_quest else 0))
        for q in quests
    ]
    history = list((await session.execute(select(QuestActivity).where(
        QuestActivity.member_id == member_id,
    ).order_by(QuestActivity.id.desc()).limit(40))).scalars().all())
    titles = {q.id: q.title for q in (await session.execute(select(Quest))).scalars()}
    # Баланс человека — руководителю он был не виден нигде: он жал «+1» и не
    # знал ни сколько у человека звёзд, ни сколько ему открылось.
    return {
        "items": items,
        "academy_enabled": member.is_active and member.status == "activist",
        "can_review": user.member_id != member_id or has_role(user, ROLE_SUPERUSER),
        "history": [{"id": h.id, "quest": titles.get(h.quest_id, "Задание"),
                     "action": h.action, "note": h.note, "created_at": h.created_at.isoformat() + "Z"} for h in history],
        "claimed": sum(i["stars_claimed"] for i in items),
        "pending": sum(i["claimable_stars"] for i in items),
    }


async def _step(session: AsyncSession, user: User, member_id: int, quest_id: int, delta: int) -> tuple:
    """Общая часть отметки и её отмены: проверки прав и поиск строки прогресса."""
    member = await _get_member(session, member_id)
    await require_academy(session, user, member)
    require_same_cell(await actor_cell(session, user), member.cell_id)

    quest = await session.get(Quest, quest_id)
    if quest is None or not quest.is_active:
        raise HTTPException(404, "Задание не найдено")

    require_student(member)
    if delta > 0 and user.member_id == member_id and not has_role(user, ROLE_SUPERUSER):
        raise AccessDenied("Своё выполнение проверяет другой руководитель")
    row = await lock_progress(session, member_id, quest_id)

    before = row.count
    # Ниже нуля не уходим: отменять нечего, а отрицательный счётчик сломал бы
    # и полоску прогресса, и подсчёт звёзд.
    ceiling = max(quest.thresholds_list(), default=0)
    if delta > 0 and row.count >= ceiling:
        raise HTTPException(409, "Задание уже выполнено полностью")
    row.count = max(0, row.count + delta)
    log_activity(session, user, row, "approved" if delta > 0 else "corrected", row.submitted_note)
    if delta > 0:
        clear_assignment(row)
    if delta > 0 and (row.pending_count or 0) > 0:
        row.pending_count = (row.pending_count or 0) - 1
        if row.pending_count == 0:
            row.submitted_at = None
            row.submitted_note = None
    row.updated_at = tz_now().replace(tzinfo=None)
    await session.commit()
    return quest, row, before


@router.post("/{quest_id}/assign")
async def assign_member_quest(
    member_id: int,
    quest_id: int,
    payload: QuestAssignmentIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    member = await _get_member(session, member_id)
    await require_academy(session, user, member)
    require_same_cell(await actor_cell(session, user), member.cell_id)
    quest = await session.get(Quest, quest_id)
    if quest is None or not quest.is_active:
        raise HTTPException(404, "Задание не найдено")
    require_student(member)
    row = await lock_progress(session, member_id, quest_id)
    if row.count >= max(quest.thresholds_list(), default=0):
        raise HTTPException(409, "Задание уже выполнено полностью")
    changed = row.assigned_by_user_id != user.id or row.assignment_note != ((payload.note or "").strip() or None)
    if changed:
        log_activity(session, user, row, "updated" if row.assigned_by_user_id else "assigned", (payload.note or "").strip() or None)
    row.assigned_by_user_id = user.id
    row.assigned_at = tz_now().replace(tzinfo=None)
    row.assignment_note = (payload.note or "").strip() or None
    row.updated_at = row.assigned_at
    await session.commit()
    if changed:
        owner = (await session.execute(select(User).where(User.member_id == member_id))).scalar_one_or_none()
        if owner is not None:
            note = f"\nКомментарий: {escape_telegram_html(row.assignment_note)}" if row.assignment_note else ""
            await notify_telegram(
                owner.telegram_id,
                f"🎓 <b>Новое задание Академии</b>\n{escape_telegram_html(quest.title)}{note}",
            )
    return _quest_dict(
        quest, row.count, row.stars_claimed, row.pending_count, row.submitted_note,
        row.assigned_by_user_id, row.assignment_note, row.earned_stars_floor,
    )


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
            outfit = _quest_dict(quest, row.count)["reward_outfit"]
            reward_hint = (f"Открыт образ «{escape_telegram_html(outfit['label'])}»." if outfit
                           else "Заберите звёзды в «Академии».")
            await notify_telegram(
                owner.telegram_id,
                f"🎉 Задание «{escape_telegram_html(quest.title)}» — "
                f"{escape_telegram_html(label)}! {reward_hint}",
            )

    return _quest_dict(quest, row.count, row.stars_claimed, row.pending_count, row.submitted_note,
                       row.assigned_by_user_id, row.assignment_note, row.earned_stars_floor)


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
    return _quest_dict(quest, row.count, row.stars_claimed, row.pending_count, row.submitted_note,
                       row.assigned_by_user_id, row.assignment_note, row.earned_stars_floor)


@router.post("/{quest_id}/reject")
async def reject_member_quest(
    member_id: int,
    quest_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    member = await _get_member(session, member_id)
    await require_academy(session, user, member)
    require_same_cell(await actor_cell(session, user), member.cell_id)
    quest = await session.get(Quest, quest_id)
    row = await lock_progress(session, member_id, quest_id)
    if quest is None or row is None or not row.pending_count:
        raise HTTPException(400, "Нет выполнения, ожидающего проверки")
    log_activity(session, user, row, "rejected", row.submitted_note)
    row.pending_count = 0
    row.submitted_at = None
    row.submitted_note = None
    row.updated_at = tz_now().replace(tzinfo=None)
    await session.commit()
    owner = (await session.execute(select(User).where(User.member_id == member_id))).scalar_one_or_none()
    if owner is not None:
        await notify_telegram(owner.telegram_id, f"↩️ Задание «{escape_telegram_html(quest.title)}» возвращено на доработку.")
    return _quest_dict(quest, row.count, row.stars_claimed, 0, None,
                       row.assigned_by_user_id, row.assignment_note, row.earned_stars_floor)


@router.post("/{quest_id}/cancel")
async def cancel_member_quest(member_id: int, quest_id: int,
                            user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)) -> dict:
    member = await _get_member(session, member_id)
    await require_academy(session, user, member)
    quest = await session.get(Quest, quest_id)
    if quest is None:
        raise HTTPException(404, "Задание не найдено")
    row = await lock_progress(session, member_id, quest_id)
    if row.assigned_by_user_id is not None:
        log_activity(session, user, row, "cancelled", row.assignment_note)
        clear_assignment(row)
    # Отправленное выполнение остаётся для проверки: отмена назначения не стирает работу.
    await session.commit()
    return _quest_dict(quest, row.count, row.stars_claimed, row.pending_count, row.submitted_note,
                       row.assigned_by_user_id, row.assignment_note, row.earned_stars_floor)
