"""Monthly allocation and issuance: the same records for coordinator and region."""
import asyncio
import re
from datetime import date
from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from api.auth import get_current_user, get_db
from database.models import User
from services import funding
from services.funding_export import workbook
from services.report_downloads import generation_slots, save_export, decode_ticket
from utils.tz import today as tz_today

router=APIRouter(prefix='/funding',tags=['funding'])
Month=Annotated[str,Field(pattern=r'^(20[0-9]{2}|2100)-(0[1-9]|1[0-2])$')]
Amount=Annotated[int,Field(strict=True,ge=0,le=2_147_483_647)]
INVALID_TEXT = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]')


def clean_text(value):
    if isinstance(value, str):
        if INVALID_TEXT.search(value):
            raise ValueError('Удалите служебные символы из текста')
        return value.strip()
    return value


def month_date(value):
    return date.fromisoformat(value+'-01') if value else tz_today().replace(day=1)


class PlanIn(BaseModel):
    month: Month
    region_id: int | None = Field(default=None,gt=0)
    amount: Amount
    note: str = Field(default='',max_length=1000)
    revision: int = Field(default=0,strict=True,ge=0,le=2_147_483_646)

    @field_validator('note', mode='before')
    @classmethod
    def trim_note(cls, value):
        return clean_text(value)


class IssueIn(BaseModel):
    plan_id: int = Field(gt=0)
    amount: int = Field(strict=True,gt=0,le=2_147_483_647)
    date: date
    comment: str = Field(min_length=3,max_length=1000)
    request_id: UUID
    allow_over_plan: bool = False

    @field_validator('comment',mode='before')
    @classmethod
    def trim_comment(cls,value):
        return clean_text(value)


class CancelIn(BaseModel):
    reason: str = Field(min_length=5,max_length=1000)
    request_id: UUID

    @field_validator('reason',mode='before')
    @classmethod
    def trim_reason(cls,value):
        return clean_text(value)


@router.get('')
async def overview(month:Month|None=None,region_id:int|None=Query(None,gt=0),user:User=Depends(get_current_user),session:AsyncSession=Depends(get_db)):
    return await funding.overview(session,user,month_date(month),region_id)


@router.put('/plan')
async def set_plan(payload:PlanIn,user:User=Depends(get_current_user),session:AsyncSession=Depends(get_db)):
    return await funding.set_plan(session,user,month_date(payload.month),payload.region_id,payload.amount,payload.note.strip(),payload.revision)


@router.get('/entries')
async def entries(month:Month|None=None,region_id:int|None=Query(None,gt=0),limit:int=Query(50,ge=1,le=100),skip:int=Query(0,ge=0,le=100000),user:User=Depends(get_current_user),session:AsyncSession=Depends(get_db)):
    return await funding.entries(session,user,month_date(month),region_id,limit,skip)


@router.post('/entries')
async def issue(payload:IssueIn,user:User=Depends(get_current_user),session:AsyncSession=Depends(get_db)):
    return await funding.issue(session,user,payload.plan_id,payload.amount,payload.date,payload.comment,str(payload.request_id),payload.allow_over_plan)


@router.post('/entries/{entry_id}/cancel')
async def cancel(entry_id:int,payload:CancelIn,user:User=Depends(get_current_user),session:AsyncSession=Depends(get_db)):
    return await funding.cancel(session,user,entry_id,payload.reason,str(payload.request_id))


@router.get('/plans/{plan_id}/history')
async def history(plan_id:int,skip:int=Query(0,ge=0,le=100000),user:User=Depends(get_current_user),session:AsyncSession=Depends(get_db)):
    return await funding.history(session,user,plan_id,skip)


@router.post('/export')
async def export(month:Month|None=None,region_id:int|None=Query(None,gt=0),user:User=Depends(get_current_user),session:AsyncSession=Depends(get_db)):
    if generation_slots.locked():raise HTTPException(429,'Файлы уже формируются. Повторите позже')
    async with generation_slots:
        # Keep the summary and journal on the same database snapshot.
        if session.get_bind().dialect.name=='postgresql':
            await session.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ'))
        data=await funding.overview(session,user,month_date(month),region_id)
        ids=[r['region_id'] for r in data['items'] if r['region_id'] is not None]
        department=any(r['region_id'] is None for r in data['items'])
        if len(ids)>50:raise HTTPException(413,'Выберите один регион для выгрузки')
        ledger=await funding.entries(session,user,month_date(month),region_id,limit=10000)
        if ledger['has_more']:raise HTTPException(413,'Слишком много выдач. Выберите один регион')
        content=await asyncio.to_thread(workbook,data,ledger['items'])
        filename='funding_'+data['month']+'.xlsx'
        ticket=await save_export(content,user.id,region_id or 0,'xlsx',filename,kind='funding',region_ids=ids,includes_department=department)
    return {'url':f"/api/reports/files/{decode_ticket(ticket)['file']}?t={ticket}",'filename':filename,'expires_in':600}
