"""«Академия» — геймификация личного кабинета (MVP, план «Персонаж и
инвентарь»). Персонаж — не собирается из частей, а выбирается из готовых
AI-сгенерированных образов (webapp/avatars/*.png). Один образ открыт всегда,
остальные открываются прогрессом по заданиям (сами задания заводятся
scripts/migrate_gamification.py, отметка выполнения — leader-side в
api/routers/quests.py).

Отдельный, не связанный с образами трек — роли (BRANCHES): группируют
задания по смыслу («Спортсмен», «Организатор» и т.д.) и строят радар-
диаграмму «к чему тяготеет» на фронте. Пересечение ступени лесенки внутри
роли делает звёзды за неё «доступными к получению» (claimable_stars в
_quest_dict) — руководитель, отмечая «+1» (api/routers/quests.py), только
двигает прогресс, а сами звёзды на баланс (Member.stars) зачисляет человек
своей кнопкой «Получить», см. claim_quest_stars ниже. Звёзды тратятся в
магазине (api/routers/shop.py) на премиальные образы и материальные
награды — премиальные образы, купленные там, подмешиваются в список
outfits ниже наравне с бесплатными."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user, get_db
from api.routers.shop import SHOP_ITEMS, owned_outfit_ids
from database.models import (
    EVENT_STATUS_CANCELLED,
    EVENT_STATUS_DONE,
    EVENT_STATUS_LABELS,
    Event,
    EventTask,
    EventTaskAssignee,
    Member,
    MemberQuestProgress,
    Quest,
    User,
)

router = APIRouter(prefix="/character", tags=["character"])

# Пороги общей «рамки» персонажа — по числу разных заданий, к которым
# притронулись хотя бы раз (не по сумме всех повторов внутри задания).
_TIER_BRONZE = 3
_TIER_SILVER = 5

# Каталог готовых образов — фиксированный, правится только здесь (новый образ
# добавляется файлом в webapp/avatars/ + записью в этом списке).
# requires — None у всегда открытого образа; "gold" у образа, открывающегося
# только когда затронуты все задания (а не по числу touched, т.к. общее
# количество заданий может меняться).
OUTFITS = [
    {"id": "polo", "label": "Студент-академист", "file": "polo.png", "requires": None},
    {"id": "hoodie", "label": "Повседневный", "file": "hoodie.png", "requires": _TIER_BRONZE},
    {"id": "smart_casual", "label": "Смарт-кэжуал", "file": "smart_casual.png", "requires": _TIER_SILVER},
    {"id": "business", "label": "Деловой", "file": "business.png", "requires": "gold"},
    # Открывается не по общему прогрессу, а за конкретное достижение —
    # requires_quest вместо requires (см. _outfit_unlocked/_hint ниже).
    {"id": "ataman", "label": "Атаман", "file": "ataman.png", "requires": None,
     "requires_quest": "Выиграть чемпионат по киле"},
]
_OUTFIT_IDS = {o["id"] for o in OUTFITS}
_DEFAULT_OUTFIT = OUTFITS[0]["id"]
_PREMIUM_OUTFITS = [i for i in SHOP_ITEMS if i["kind"] == "outfit"]
_ALL_OUTFIT_IDS = _OUTFIT_IDS | {i["id"] for i in _PREMIUM_OUTFITS}

# Роли — группировка заданий для звёздочек и радар-диаграммы «к чему тяготеет».
# Не влияют на открытие образов выше (те по-прежнему от общего числа
# затронутых заданий) — отдельный, параллельный трек прогресса.
BRANCHES = [
    {
        "id": "athlete",
        "label": "Сила",
        "titles": ["Сыграть в килу", "Встать в стенку", "Выиграть чемпионат по киле"],
    },
    {
        "id": "organizer",
        "label": "Лидерство",
        "titles": [
            "Посетить мероприятие",
            "Помочь в организации мероприятия",
            "Привести нового участника",
            "Расклеить стикеры",
            "Стать наставником новичка",
            "Организовать добровольческую акцию",
            "Провести дискуссионный клуб",
        ],
    },
    {"id": "media", "label": "Творчество", "titles": ["Написать пост в соцсети об отделении", "Сделать фотоисторию мероприятия"]},
    {"id": "catechist", "label": "Духовность", "titles": ["Сходить на службу с Академистами"]},
    {"id": "knowledge", "label": "Знание", "titles": ["Прочитать книгу", "Провести экскурсию по истории города", "Выступить с короткой лекцией"]},
    {"id": "honor", "label": "Честь", "titles": ["Помочь другому участнику Братства"]},
]
_BRANCH_BY_TITLE = {title: b for b in BRANCHES for title in b["titles"]}
# Задания, чья награда — конкретный образ, а не звёзды (см. OUTFITS выше) —
# для них не начисляем и не показываем звёзды, только сам факт разблокировки.
_OUTFIT_BY_QUEST_TITLE = {
    o["requires_quest"]: {"label": o["label"], "image": f"/static/avatars/{o['file']}"}
    for o in OUTFITS
    if o.get("requires_quest")
}


async def _own_member(user: User, session: AsyncSession, *, lock: bool = False) -> Member:
    if user.member_id is None:
        raise HTTPException(404, "У этого аккаунта нет личного кабинета — он не привязан к «Составу»")
    stmt = select(Member).where(Member.id == user.member_id)
    if lock:
        stmt = stmt.with_for_update()
    member = (await session.execute(stmt)).scalar_one_or_none()
    if member is None:
        raise HTTPException(404, "Запись в составе не найдена")
    return member


def _earned_stars(thresholds: list[int], rewards: list[int], count: int) -> int:
    """Сколько звёзд «заработано» на текущий прогресс — сумма цен пройденных
    ступеней (лесенка «1,3,10» при count=10 -> 1+3+10=14), без учёта того,
    сколько из них уже забрано (см. claimable_stars).

    Цена ступени обычно равна её порогу, но не обязана: у заданий, где порог
    измеряет объём работы, а не заслугу, она задаётся отдельно
    (Quest.rewards_list).
    """
    return sum(reward for threshold, reward in zip(thresholds, rewards) if threshold <= count)


def _quest_dict(
    quest: Quest, count: int, stars_claimed: int = 0,
    pending_count: int = 0, submitted_note: str | None = None,
    assigned_by_user_id: int | None = None, assignment_note: str | None = None,
) -> dict:
    thresholds = quest.thresholds_list()
    rewards = quest.rewards_list()
    next_target = next((t for t in thresholds if count < t), None)
    next_reward = next(
        (reward for threshold, reward in zip(thresholds, rewards) if count < threshold), None
    )
    branch = _BRANCH_BY_TITLE.get(quest.title)
    return {
        "id": quest.id,
        "title": quest.title,
        "description": quest.description,
        "emoji": quest.emoji,
        "count": count,
        "thresholds": thresholds,
        "next_target": next_target,
        "completed": next_target is None,
        "progress_label": f"{count}/{next_target}" if next_target is not None else f"Выполнено ({count})",
        "branch_id": branch["id"] if branch else None,
        "branch_label": branch["label"] if branch else None,
        "reward_outfit": _OUTFIT_BY_QUEST_TITLE.get(quest.title),
        # Не ниже нуля: отметку можно снять (quests.py::decrement), а забранные
        # звёзды назад не отбирают — иначе после отмены выходило бы «−3 ★».
        "claimable_stars": (
            0 if quest.title in _OUTFIT_BY_QUEST_TITLE
            else max(0, _earned_stars(thresholds, rewards, count) - stars_claimed)
        ),
        "stars_claimed": stars_claimed,
        "pending_count": pending_count,
        "submitted_note": submitted_note,
        "assigned": assigned_by_user_id is not None,
        "assigned_by_user_id": assigned_by_user_id,
        "assignment_note": assignment_note,
        # Сколько откроет следующая ступень. Обычно это её же номер, но у
        # заданий со своей ценой — цена (Quest.rewards_list).
        "next_reward": next_reward,
    }


def _outfit_unlocked(o: dict, touched: int, tier: str | None, quest_items: list[dict]) -> bool:
    requires_quest = o.get("requires_quest")
    if requires_quest is not None:
        return any(q["title"] == requires_quest and q["completed"] for q in quest_items)
    requires = o["requires"]
    if requires is None:
        return True
    if requires == "gold":
        return tier == "gold"
    return touched >= requires


async def _character_payload(session: AsyncSession, member: Member) -> dict:
    quests = list(
        (await session.execute(select(Quest).where(Quest.is_active.is_(True)).order_by(Quest.position))).scalars().all()
    )
    progress_rows = (
        await session.execute(select(MemberQuestProgress).where(MemberQuestProgress.member_id == member.id))
    ).scalars().all()
    progress_by_quest = {row.quest_id: row for row in progress_rows}

    quest_items = [
        _quest_dict(q, (progress_by_quest[q.id].count if q.id in progress_by_quest else 0),
                    (progress_by_quest[q.id].stars_claimed if q.id in progress_by_quest else 0),
                    (progress_by_quest[q.id].pending_count if q.id in progress_by_quest else 0),
                    (progress_by_quest[q.id].submitted_note if q.id in progress_by_quest else None),
                    (progress_by_quest[q.id].assigned_by_user_id if q.id in progress_by_quest else None),
                    (progress_by_quest[q.id].assignment_note if q.id in progress_by_quest else None))
        for q in quests
    ]
    touched = sum(1 for item in quest_items if item["count"] > 0)
    total = len(quest_items)
    if total and touched >= total:
        tier = "gold"
    elif touched >= _TIER_SILVER:
        tier = "silver"
    elif touched >= _TIER_BRONZE:
        tier = "bronze"
    else:
        tier = None

    def _hint(o: dict) -> str | None:
        requires_quest = o.get("requires_quest")
        if requires_quest is not None:
            return f"Открывается за «{requires_quest}»"
        requires = o["requires"]
        if requires is None:
            return None
        if requires == "gold":
            return "Открывается, когда затронуты все задания" + (f" ({total})" if total else "")
        return f"Открывается после {requires} разных заданий"

    outfits = [
        {
            "id": o["id"],
            "label": o["label"],
            "image": f"/static/avatars/{o['file']}",
            "unlocked": _outfit_unlocked(o, touched, tier, quest_items),
            "hint": _hint(o),
        }
        for o in OUTFITS
    ]

    purchased = await owned_outfit_ids(session, member.id)
    outfits += [
        {
            "id": i["id"],
            "label": i["label"],
            "image": f"/static/avatars/{i['file']}",
            "unlocked": i["id"] in purchased,
            "hint": None if i["id"] in purchased else f"Доступно в магазине за {i['price']} ⭐",
        }
        for i in _PREMIUM_OUTFITS
    ]

    active_outfit = member.avatar_outfit if member.avatar_outfit in _ALL_OUTFIT_IDS else _DEFAULT_OUTFIT

    # Радар «к чему тяготеет» — по каждой роли доля выполненного: сумма count,
    # ограниченная потолком лесенки, к сумме потолков всех заданий роли, 0-100.
    # Задачи мероприятий сюда больше не входят: радар про склонность человека,
    # а поручение говорит о том, что дал руководитель, а не к чему тянет.
    radar = []
    branch_summaries = []
    for b in BRANCHES:
        branch_quests = [q for q in quest_items if q["branch_id"] == b["id"]]
        if not branch_quests:
            continue
        earned = sum(min(q["count"], max(q["thresholds"]) if q["thresholds"] else 0) for q in branch_quests)
        possible = sum(max(q["thresholds"]) if q["thresholds"] else 0 for q in branch_quests)
        radar.append({"id": b["id"], "label": b["label"], "value": round(earned / possible * 100) if possible else 0})
        branch_summaries.append({
            "id": b["id"],
            "label": b["label"],
            "claimable_stars": sum(q["claimable_stars"] for q in branch_quests),
        })

    return {
        "outfits": outfits,
        "active_outfit": active_outfit,
        "quests": quest_items,
        "quests_touched": touched,
        "quests_total": total,
        "tier": tier,
        "stars": member.stars,
        "radar": radar,
        "branches": branch_summaries,
    }


@router.get("/me")
async def get_character(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)
) -> dict:
    member = await _own_member(user, session)
    return await _character_payload(session, member)


class AvatarPatch(BaseModel):
    outfit: str = Field(...)


class QuestSubmissionIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


@router.patch("/me/avatar")
async def update_avatar(
    payload: AvatarPatch,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    member = await _own_member(user, session, lock=True)
    if payload.outfit not in _ALL_OUTFIT_IDS:
        raise HTTPException(400, "Неизвестный образ")

    data = await _character_payload(session, member)
    unlocked_ids = {o["id"] for o in data["outfits"] if o["unlocked"]}
    if payload.outfit not in unlocked_ids:
        raise HTTPException(400, "Этот образ ещё не открыт")

    member.avatar_outfit = payload.outfit
    await session.commit()
    return await _character_payload(session, member)


@router.post("/me/quests/{quest_id}/claim")
async def claim_quest_stars(
    quest_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    member = await _own_member(user, session, lock=True)
    quest = await session.get(Quest, quest_id)
    if quest is None:
        raise HTTPException(404, "Задание не найдено")
    if quest.title in _OUTFIT_BY_QUEST_TITLE:
        raise HTTPException(400, "У этого задания награда — образ, а не звёзды")

    row = (
        await session.execute(
            select(MemberQuestProgress).where(
                MemberQuestProgress.member_id == member.id, MemberQuestProgress.quest_id == quest_id
            ).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(400, "По этому заданию ещё нечего получать")

    claimable = _earned_stars(quest.thresholds_list(), quest.rewards_list(), row.count) - row.stars_claimed
    if claimable <= 0:
        raise HTTPException(400, "По этому заданию ещё нечего получать")

    row.stars_claimed += claimable
    member.stars += claimable
    await session.commit()
    return await _character_payload(session, member)


@router.post("/me/quests/{quest_id}/submit")
async def submit_quest_for_review(
    quest_id: int,
    payload: QuestSubmissionIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    member = await _own_member(user, session, lock=True)
    quest = await session.get(Quest, quest_id)
    if quest is None or not quest.is_active:
        raise HTTPException(404, "Задание не найдено")
    row = (await session.execute(
        select(MemberQuestProgress).where(
            MemberQuestProgress.member_id == member.id,
            MemberQuestProgress.quest_id == quest_id,
        ).with_for_update()
    )).scalar_one_or_none()
    if row is None:
        row = MemberQuestProgress(member_id=member.id, quest_id=quest_id, count=0)
        session.add(row)
    if row.pending_count:
        raise HTTPException(409, "Выполнение уже ждёт проверки")
    row.pending_count = 1
    row.submitted_note = (payload.note or "").strip() or None
    from utils.tz import now as tz_now
    row.submitted_at = tz_now().replace(tzinfo=None)
    await session.commit()
    return await _character_payload(session, member)
