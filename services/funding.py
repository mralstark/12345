"""Monthly funding is an allocation ledger, independent of regional transactions."""
import json
from datetime import date
from sqlalchemy import case, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError, OperationalError
from fastapi import HTTPException
from database.models import (BureauMember, BureauRegion, CoordinatorRegion, FundingPlan, FundingPlanAudit, FundingEntry,
                             Region, User, ROLE_COORDINATOR, ROLE_FEDERAL, ROLE_SUPERUSER)
from utils.access import AccessDenied, accessible_region_ids, actor_cell
from utils.permissions import has_any_role
from utils.tz import today as tz_today, iso_utc

DEPARTMENT = 'Отдел регионального развития'


async def serialize_write(session):
    # SQLite ignores FOR UPDATE. Acquire its writer reservation BEFORE the
    # authorization/aggregate reads, so two issuances cannot share an old balance.
    # PostgreSQL instead locks the specific plan below, allowing other regions
    # to work concurrently. Callers use a fresh session for each financial write.
    if session.get_bind().dialect.name == 'sqlite':
        try:
            await session.execute(text('BEGIN IMMEDIATE'))
        except OperationalError:
            await session.rollback()
            raise HTTPException(409, 'Реестр занят. Повторите запрос через несколько секунд') from None


async def managed_regions(session, user, readable):
    if has_any_role(user, (ROLE_FEDERAL, ROLE_SUPERUSER)):
        return set(readable)
    managed = set()
    if has_any_role(user, (ROLE_COORDINATOR,)):
        managed.update((await session.execute(select(CoordinatorRegion.region_id).where(
            CoordinatorRegion.coordinator_user_id == user.id, CoordinatorRegion.region_id.in_(readable)))).scalars())
    managed.update((await session.execute(select(BureauRegion.region_id).join(BureauMember,
        BureauMember.id == BureauRegion.bureau_member_id).where(
        BureauMember.user_id == user.id, BureauRegion.region_id.in_(readable)))).scalars())
    return managed


async def scope(session, user):
    if not user.is_active:
        raise AccessDenied('Учётная запись отключена')
    if await actor_cell(session, user) is not None:
        raise AccessDenied('Финансирование доступно руководителю региона и выше')
    ids = await accessible_region_ids(session, user)
    manager = bool(await managed_regions(session, user, ids)) or has_any_role(user, (ROLE_FEDERAL, ROLE_SUPERUSER))
    # The department is shared by active funding coordinators, never granted
    # merely by regional leadership or by an unassigned bureau membership.
    department = manager
    if not ids and not department:
        raise AccessDenied('Нет доступных регионов')
    return ids, manager, department


async def authorize(session, user, region_id, *, editing=False):
    ids, manager, department = await scope(session, user)
    if (region_id is None and not department) or (region_id is not None and region_id not in ids):
        raise AccessDenied('Нет доступа к этому бюджету')
    if editing and (not manager or (region_id is not None and region_id not in await managed_regions(session,user,ids))):
        raise AccessDenied('План и выдачи ведёт координатор регионов')


async def load_plan(session, user, plan_id, *, editing=False, lock=False):
    stmt = select(FundingPlan).where(FundingPlan.id == plan_id)
    if lock:
        stmt = stmt.with_for_update()
    plan = (await session.execute(stmt)).scalar_one_or_none()
    if plan is None:
        raise HTTPException(404, 'План не найден')
    await authorize(session, user, plan.region_id, editing=editing)
    return plan


def plan_dict(plan):
    return None if plan is None else {'id':plan.id, 'amount':plan.amount, 'note':plan.note or '',
                                     'revision':plan.revision, 'updated_at':iso_utc(plan.updated_at)}


async def overview(session, user, month, region_id=None):
    ids, manager, department = await scope(session, user)
    managed = await managed_regions(session,user,ids)
    if region_id is not None:
        await authorize(session, user, region_id)
        ids, department = [region_id], False
    regions = list((await session.execute(select(Region).where(Region.id.in_(ids)).order_by(Region.id))).scalars())
    regions.sort(key=lambda r:(r.name.casefold(),r.id))
    allowed = or_(FundingPlan.region_id.in_(ids), FundingPlan.region_id.is_(None)) if department else FundingPlan.region_id.in_(ids)
    plans = list((await session.execute(select(FundingPlan).where(FundingPlan.month == month, allowed))).scalars())
    by_region = {p.region_id:p for p in plans}
    totals = dict((await session.execute(select(FundingEntry.plan_id, func.sum(case(
        (FundingEntry.kind == 'issue', FundingEntry.amount), else_=-FundingEntry.amount))).where(
        FundingEntry.plan_id.in_([p.id for p in plans])).group_by(FundingEntry.plan_id))).all())
    rows=[]
    for rid,name in ([(None,DEPARTMENT)] if department else []) + [(r.id,r.name) for r in regions]:
        p=by_region.get(rid); issued=totals.get(p.id,0) if p else 0
        rows.append({'region_id':rid,'name':name,'plan':plan_dict(p),'issued':issued,
                     'remaining':p.amount-issued if p else None,'can_manage':department if rid is None else rid in managed})
    return {'month':month.strftime('%Y-%m'),'items':rows,'can_manage':manager,
            'totals':{'planned':sum(p.amount for p in plans),'issued':sum(totals.values()),
                      'remaining':sum(p.amount for p in plans)-sum(totals.values()),
                      'without_plan':sum(r['plan'] is None for r in rows)}}


async def set_plan(session, user, month, region_id, amount, note, revision):
    await serialize_write(session)
    await authorize(session,user,region_id,editing=True)
    plan=(await session.execute(select(FundingPlan).where(FundingPlan.month==month,
        FundingPlan.region_id==region_id if region_id is not None else FundingPlan.region_id.is_(None)).with_for_update())).scalar_one_or_none()
    if (plan is None and revision != 0) or (plan is not None and revision != plan.revision):
        raise HTTPException(409,'План уже изменился. Обновите экран')
    before={'amount':plan.amount,'note':plan.note or ''} if plan else None
    if before == {'amount':amount,'note':note}:
        return plan_dict(plan)
    try:
        if plan is None:
            plan=FundingPlan(month=month,region_id=region_id,amount=amount,note=note or None,revision=1,updated_by_id=user.id)
            session.add(plan);await session.flush()
        else:
            changed=await session.execute(update(FundingPlan).where(FundingPlan.id==plan.id,FundingPlan.revision==revision).values(
                amount=amount,note=note or None,revision=revision+1,updated_by_id=user.id,updated_at=func.now()).execution_options(synchronize_session=False))
            if changed.rowcount != 1:
                await session.rollback();raise HTTPException(409,'План уже изменился. Обновите экран')
            await session.refresh(plan)
        session.add(FundingPlanAudit(plan_id=plan.id,revision=plan.revision,actor_id=user.id,
                    payload=json.dumps({'before':before,'after':{'amount':amount,'note':note}},ensure_ascii=False)))
        await session.commit();await session.refresh(plan)
    except IntegrityError:
        await session.rollback();raise HTTPException(409,'План уже создан другим пользователем. Обновите экран') from None
    return plan_dict(plan)


async def duplicate(session, user, request_id, values):
    row=(await session.execute(select(FundingEntry).where(FundingEntry.created_by_id==user.id,FundingEntry.request_id==request_id))).scalar_one_or_none()
    if row:
        if any(getattr(row,key)!=value for key,value in values.items()):
            raise HTTPException(409,'Этот запрос уже использован с другими данными. Обновите список выдач')
        return {'id':row.id,'replayed':True}


async def issue(session,user,plan_id,amount,issued_on,comment,request_id,allow_over_plan=False):
    await serialize_write(session)
    plan=await load_plan(session,user,plan_id,editing=True,lock=True)
    if issued_on.replace(day=1)!=plan.month:
        raise HTTPException(422,'Дата выдачи должна относиться к месяцу плана')
    if issued_on>tz_today():
        raise HTTPException(422,'Будущую выдачу нельзя записать как уже состоявшуюся')
    values={'plan_id':plan.id,'kind':'issue','cancels_id':None,'amount':amount,'date':issued_on,'comment':comment}
    replay=await duplicate(session,user,request_id,values)
    if replay:return replay
    total=(await session.execute(select(func.coalesce(func.sum(case((FundingEntry.kind=='issue',FundingEntry.amount),else_=-FundingEntry.amount)),0)).where(FundingEntry.plan_id==plan.id))).scalar_one()
    if total+amount>plan.amount and not allow_over_plan:
        raise HTTPException(409,'Сумма превысит план. Подтвердите запись сверх плана')
    row=FundingEntry(**values,created_by_id=user.id,request_id=request_id)
    session.add(row)
    try:
        await session.commit();await session.refresh(row)
    except IntegrityError:
        await session.rollback()
        replay=await duplicate(session,user,request_id,values)
        if replay:return replay
        raise HTTPException(409,'Выдача уже изменилась. Обновите экран') from None
    return {'id':row.id,'replayed':False}


async def cancel(session,user,entry_id,reason,request_id):
    await serialize_write(session)
    original=await session.get(FundingEntry,entry_id)
    if original is None:raise HTTPException(404,'Выдача не найдена')
    await load_plan(session,user,original.plan_id,editing=True,lock=True)
    if original.kind!='issue':raise HTTPException(422,'Можно отменить только исходную выдачу')
    values={'plan_id':original.plan_id,'kind':'cancel','cancels_id':original.id,'amount':original.amount,'comment':reason}
    # Cancellation date is chosen by the server, but retries across midnight stay idempotent.
    replay=await duplicate(session,user,request_id,values)
    if replay:return replay
    exists=(await session.execute(select(FundingEntry.id).where(FundingEntry.cancels_id==original.id))).first()
    if exists:raise HTTPException(409,'Выдача уже отменена. Обновите экран')
    row=FundingEntry(**values,date=tz_today(),created_by_id=user.id,request_id=request_id)
    session.add(row)
    try:
        await session.commit();await session.refresh(row)
    except IntegrityError:
        await session.rollback()
        replay=await duplicate(session,user,request_id,values)
        if replay:return replay
        raise HTTPException(409,'Выдача уже отменена. Обновите экран') from None
    return {'id':row.id,'replayed':False}


async def entries(session,user,month,region_id=None,limit=50,skip=0):
    ids,manager,department=await scope(session,user)
    managed=await managed_regions(session,user,ids)
    if region_id is not None:
        await authorize(session,user,region_id);ids,department=[region_id],False
    allowed=or_(FundingPlan.region_id.in_(ids),FundingPlan.region_id.is_(None)) if department else FundingPlan.region_id.in_(ids)
    records=(await session.execute(select(FundingEntry,FundingPlan.region_id,Region.name,User.full_name)
        .join(FundingPlan,FundingPlan.id==FundingEntry.plan_id).outerjoin(Region,Region.id==FundingPlan.region_id)
        .join(User,User.id==FundingEntry.created_by_id).where(FundingPlan.month==month,allowed)
        .order_by(FundingEntry.date.desc(),FundingEntry.id.desc()).offset(skip).limit(limit+1))).all()
    ids_shown=[e.id for e,_,_,_ in records[:limit] if e.kind=='issue']
    cancellations={entry.cancels_id:entry.id for entry in (await session.execute(select(FundingEntry).where(FundingEntry.cancels_id.in_(ids_shown)))).scalars()}
    return {'has_more':len(records)>limit,'items':[{'id':e.id,'plan_id':e.plan_id,'region_id':rid,'region_name':name if rid is not None else DEPARTMENT,
             'date':e.date.isoformat(),'amount':e.amount,'kind':e.kind,'comment':e.comment,'actor':author,
             'cancels_id':e.cancels_id,'cancelled':e.id in cancellations,
             'can_cancel':(department if rid is None else rid in managed) and e.kind=='issue' and e.id not in cancellations} for e,rid,name,author in records[:limit]]}


async def history(session,user,plan_id,skip=0):
    await load_plan(session,user,plan_id)
    rows=(await session.execute(select(FundingPlanAudit,User.full_name).join(User,User.id==FundingPlanAudit.actor_id)
         .where(FundingPlanAudit.plan_id==plan_id).order_by(FundingPlanAudit.revision.desc()).offset(skip).limit(51))).all()
    return {'has_more':len(rows)>50,'items':[{'revision':r.revision,'at':iso_utc(r.created_at),'actor':name,**json.loads(r.payload)} for r,name in rows[:50]]}
