"""Opt-in preparation, independent from archived quests and membership ranks."""
import json
import logging
from datetime import timezone

from fastapi import HTTPException
from sqlalchemy import select, update

from database.models import Member, MentorPreparation, Region, User
from services.academy import require_academy
from utils.access import AccessDenied
from utils.tz import now

STEPS = [
    {"id": "goal", "title": "Моя цель", "purpose": "Понять, в чём именно вам поможет наставник.",
     "prompt": "Что вы хотите научиться делать? Почему это важно сейчас? Сколько времени в неделю готовы уделять подготовке? Не указывайте личные секреты или данные других людей."},
    {"id": "plan", "title": "Небольшое дело", "purpose": "Перевести цель в посильную практику.",
     "prompt": "Предложите небольшое полезное дело: помощь в событии, выступление, материал или собственный проект. Опишите результат, первые действия, срок и нужную помощь. Сначала согласуйте план с проверяющим."},
    {"id": "practice", "title": "Практика и результат", "purpose": "Попробовать действовать самостоятельно и просить помощь вовремя.",
     "prompt": "Выполните согласованный план. Что получилось? Какой результат можно показать? Что пришлось изменить? Можно указать ссылку на работу; документы и персональные данные других людей прикладывать не нужно."},
    {"id": "reflection", "title": "Разбор и следующий шаг", "purpose": "Сформулировать запрос для будущей работы с наставником.",
     "prompt": "Чему вы научились? Что было трудно? Какая обратная связь помогла? Какие вопросы хотите обсудить с наставником и какой следующий результат для вас важен?"},
]
LABELS = {"invited": "Приглашение", "active": "В подготовке", "paused": "На паузе", "withdrawn": "Участие завершено",
          "ready": "Подготовка завершена", "recommended": "Рекомендован к менторству", "waitlist": "Ожидает места"}


def timestamp():
    return now().astimezone(timezone.utc).isoformat()


def initial(note, member_id):
    return {"version": 1, "member_id": member_id, "status": "invited", "invitation": note, "steps": [
        {"status": "draft", "response": "", "feedback": ""} for _ in STEPS], "history": []}


def unpack(row):
    data = json.loads(row.payload)
    if data.get("version") != 1 or data.get("member_id") != row.member_id or len(data.get("steps", [])) != len(STEPS) or data.get("status") not in LABELS:
        raise HTTPException(409, "Версия программы не поддерживается")
    return data


def eligible(member):
    return member.is_active and member.status == "activist"


async def access(session, actor, member, *, managing=False):
    region = await session.get(Region, member.region_id)
    if region is None or not region.is_active:
        raise AccessDenied("Регион недоступен")
    if managing or actor.member_id != member.id:
        await require_academy(session, actor, member)


async def load(session, actor, row_id, *, managing=False):
    row = (await session.execute(select(MentorPreparation).where(MentorPreparation.id == row_id).with_for_update())).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Подготовка не найдена")
    member = await session.get(Member, row.member_id)
    if member is None:
        raise HTTPException(404, "Участник не найден")
    await access(session, actor, member, managing=managing)
    return row, member, unpack(row)


def require_current(member):
    if not eligible(member):
        raise HTTPException(409, "Подготовка доступна действующим корпорантам; история сохранена")


async def save(session, row, data, revision, actor, action, *, step=None, note=""):
    # Compare-and-swap also protects SQLite, where FOR UPDATE is not available.
    data["history"].append({"action": action, "actor_id": actor.id, "at": timestamp(), "step": step, "note": note})
    data["history"] = data["history"][-80:]
    result = await session.execute(update(MentorPreparation).where(
        MentorPreparation.id == row.id, MentorPreparation.revision == revision,
    ).values(payload=json.dumps(data, ensure_ascii=False), revision=revision + 1,
             invited_by_user_id=actor.id if action == "reinvite" else row.invited_by_user_id,
             updated_at=now().replace(tzinfo=None)).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        await session.rollback()
        raise HTTPException(409, "Запись уже изменилась. Обновите экран перед повторным действием")
    await session.commit()
    await session.refresh(row)


def present(row, member, *, details=True):
    data = unpack(row)
    result = {"id": row.id, "member_id": member.id, "name": member.full_name, "region_id": member.region_id,
              "revision": row.revision, "status": data["status"], "status_label": LABELS[data["status"]],
              "completed": sum(s["status"] == "approved" for s in data["steps"]), "total": len(STEPS),
              "pending": sum(s["status"] == "submitted" for s in data["steps"]), "eligible": eligible(member),
              "updated_at": row.updated_at.isoformat(), "updated_label": "Изменено"}
    if details:
        result.update(invitation=data["invitation"], decision=data.get("decision", ""), history=data["history"][-20:],
                      steps=[{**template, **state, "open": all(s["status"] == "approved" for s in data["steps"][:i])}
                             for i, (template, state) in enumerate(zip(STEPS, data["steps"]))])
    return result


async def notify_user(session, member_id, text):
    from utils.notify import notify_telegram
    account = (await session.execute(select(User).where(User.member_id == member_id, User.is_active.is_(True)))).scalars().first()
    if account and account.telegram_id:
        await notify_telegram(account.telegram_id, text)


async def deliver_notice(member_id, *, reviewer_id=None, invitation=False):
    """Best effort after HTTP completion; the in-app dossier is authoritative."""
    from database.db import async_session
    try:
        async with async_session() as session:
            member = await session.get(Member, member_id)
            if member is None:
                return
            if reviewer_id is not None:
                reviewer = await session.get(User, reviewer_id)
                if not reviewer or not reviewer.is_active or reviewer.member_id == member.id:
                    return
                await access(session, reviewer, member, managing=True)
                from utils.notify import notify_telegram
                if reviewer.telegram_id:
                    await notify_telegram(reviewer.telegram_id, "В Академии обновлена подготовка участника. Откройте «Подготовка → Участники».")
            else:
                message = ("В Академии появилось приглашение на подготовку к менторству. Откройте «Академия → Подготовка». Участие добровольное."
                           if invitation else "В Академии обновлена ваша подготовка к менторству. Посмотрите обратную связь в разделе «Подготовка».")
                await notify_user(session, member_id, message)
    except AccessDenied:
        pass
    except Exception as error:
        logging.getLogger(__name__).warning("Preparation notification failed: %s", type(error).__name__)
