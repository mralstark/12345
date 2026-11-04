"""Preparation is invited explicitly; consent and review never change membership."""
import json

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks, Path as PathParam
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user, get_db
from database.models import Member, MentorPreparation, User, Region
from services.academy import can_manage_academy
from services import mentor_preparation as prep
from utils.access import AccessDenied, accessible_region_ids, require_view

router = APIRouter(prefix="/academy/preparation", tags=["academy"])


class InviteIn(BaseModel):
    member_id: int = Field(gt=0, le=2**31-1)
    note: str = Field(min_length=5, max_length=2000)


class ChangeIn(BaseModel):
    revision: int = Field(ge=1, le=2**31-2)
    action: str = Field(max_length=24)
    step: int | None = Field(default=None, ge=0, le=3)
    text: str = Field(default="", max_length=4000)


@router.get("/me")
async def mine(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)):
    row = (await session.execute(select(MentorPreparation).where(MentorPreparation.member_id == user.member_id))).scalar_one_or_none() if user.member_id else None
    item = None
    if row:
        member = await session.get(Member, row.member_id)
        region = await session.get(Region, member.region_id) if member else None
        if region and region.is_active:
            await prep.access(session, user, member)
            item = prep.present(row, member)
    return {"item": item, "can_manage": can_manage_academy(user)}


@router.get("/participants")
async def participants(offset: int = Query(0, ge=0, le=100000), user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)):
    if not can_manage_academy(user):
        raise AccessDenied("Нет доступа к управлению подготовкой")
    regions = await accessible_region_ids(session, user)
    rows = (await session.execute(select(MentorPreparation, Member).join(Member).where(Member.region_id.in_(regions))
                                  .order_by(MentorPreparation.updated_at.desc(), MentorPreparation.id.desc()).offset(offset).limit(51))).all()
    return {"items": [prep.present(r, m, details=False) for r, m in rows[:50]], "has_more": len(rows) > 50}


@router.get("/candidates")
async def candidates(region_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)):
    if not can_manage_academy(user):
        raise AccessDenied("Нет доступа к управлению подготовкой")
    await require_view(session, user, region_id)
    rows = (await session.execute(select(Member).join(User, User.member_id == Member.id).outerjoin(MentorPreparation, MentorPreparation.member_id == Member.id)
        .where(Member.region_id == region_id, Member.is_active.is_(True), Member.status == "activist", User.is_active.is_(True),
               User.id != user.id, MentorPreparation.id.is_(None)).order_by(Member.id).limit(501))).scalars().all()
    if len(rows) > 500:
        raise HTTPException(409, "Для большого региона приглашайте через карточку участника")
    return {"items": [{"id": m.id, "name": m.full_name} for m in sorted(rows, key=lambda m: (m.full_name.casefold(), m.id))]}


@router.post("/invite")
async def invite(payload: InviteIn, background: BackgroundTasks, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)):
    member = (await session.execute(select(Member).where(Member.id == payload.member_id).with_for_update())).scalar_one_or_none()
    if member is None:
        raise HTTPException(404, "Участник не найден")
    await prep.access(session, user, member, managing=True)
    prep.require_current(member)
    if user.member_id == member.id:
        raise AccessDenied("Приглашение оформляет другой руководитель")
    account = (await session.execute(select(User.id).where(User.member_id == member.id, User.is_active.is_(True)))).first()
    if not account:
        raise HTTPException(409, "Сначала участник должен зарегистрироваться")
    existing = (await session.execute(select(MentorPreparation).where(MentorPreparation.member_id == member.id))).scalar_one_or_none()
    if existing:
        return prep.present(existing, member)
    note = payload.note.strip()
    if len(note) < 5:
        raise HTTPException(422, "Опишите цель приглашения и к кому обращаться")
    data = prep.initial(note, member.id)
    data["history"].append({"action": "invited", "actor_id": user.id, "at": prep.timestamp(), "step": None, "note": note})
    row = MentorPreparation(member_id=member.id, invited_by_user_id=user.id, payload=json.dumps(data, ensure_ascii=False), revision=1)
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, "Приглашение уже создано. Обновите список") from None
    await session.refresh(row)
    background.add_task(prep.deliver_notice, member.id, invitation=True)
    return prep.present(row, member)


@router.get("/{row_id}")
async def detail(row_id: int = PathParam(gt=0, le=2**31-1), user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)):
    row, member, data = await prep.load(session, user, row_id)
    return {**prep.present(row, member), "can_review": can_manage_academy(user) and user.member_id != member.id}


@router.post("/{row_id}")
async def change(payload: ChangeIn, background: BackgroundTasks, row_id: int = PathParam(gt=0, le=2**31-1), user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)):
    row, member, data = await prep.load(session, user, row_id)
    own = user.member_id == member.id
    action, text = payload.action, payload.text.strip()
    if payload.revision != row.revision:
        raise HTTPException(409, "Запись уже изменилась. Обновите экран")
    prep.require_current(member)
    if own:
        if action == "accept" and data["status"] == "invited":
            data["status"] = "ready" if all(s["status"] == "approved" for s in data["steps"]) else "active"
        elif action == "pause" and data["status"] in {"active", "ready", "recommended", "waitlist"}:
            data["resume_status"], data["status"] = data["status"], "paused"
        elif action == "resume" and data["status"] == "paused":
            data["status"] = data.pop("resume_status", "active")
        elif action == "withdraw" and data["status"] != "withdrawn":
            data["status"] = "withdrawn"
        elif action in {"draft", "submit"} and data["status"] == "active" and payload.step is not None:
            if not all(s["status"] == "approved" for s in data["steps"][:payload.step]):
                raise HTTPException(409, "Сначала завершите предыдущий шаг")
            step = data["steps"][payload.step]
            if step["status"] in {"approved", "submitted"}:
                raise HTTPException(409, "Этот шаг уже принят или находится на проверке")
            if action == "submit" and len(text) < 5:
                raise HTTPException(422, "Добавьте содержательный ответ")
            step.update(response=text, status="submitted" if action == "submit" else "draft")
            if action == "submit":
                step["submitted_at"] = prep.timestamp()
        else:
            raise HTTPException(409, "Действие сейчас недоступно")
    else:
        await prep.access(session, user, member, managing=True)
        if action in {"approve", "changes"} and data["status"] == "active" and payload.step is not None:
            step = data["steps"][payload.step]
            if step["status"] != "submitted":
                raise HTTPException(409, "Нет ответа на проверке")
            if len(text) < 3 or len(text) > 2000:
                raise HTTPException(422, "Добавьте обратную связь: от 3 до 2000 символов")
            step.update(status="approved" if action == "approve" else "changes", feedback=text,
                        reviewed_at=prep.timestamp(), reviewed_by=user.id)
            if all(s["status"] == "approved" for s in data["steps"]):
                data["status"] = "ready"
        elif action in {"recommend", "waitlist"} and data["status"] in {"ready", "waitlist", "recommended"}:
            if not all(s["status"] == "approved" for s in data["steps"]) or not 3 <= len(text) <= 2000:
                raise HTTPException(422, "Завершите проверку всех шагов и добавьте пояснение")
            data.update(status="recommended" if action == "recommend" else "waitlist", decision=text)
        elif action == "reinvite" and data["status"] == "withdrawn" and 5 <= len(text) <= 2000:
            data.update(status="invited", invitation=text)
        else:
            raise HTTPException(409, "Действие сейчас недоступно")
    await prep.save(session, row, data, payload.revision, user, action, step=payload.step, note=text)
    if not own:
        background.add_task(prep.deliver_notice, member.id)
    elif action in {"submit", "withdraw"} and row.invited_by_user_id is not None:
        background.add_task(prep.deliver_notice, member.id, reviewer_id=row.invited_by_user_id)
    return prep.present(row, member)
