"""Scoped cell workbooks. Personal data is decrypted only after authorization."""
import asyncio
from io import BytesIO
from decimal import Decimal
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from sqlalchemy import select
from fastapi import HTTPException
from database.models import UniversityCell, University, Region, Member, User, Transaction, MEMBER_STATUS_LABELS
from utils.access import accessible_region_ids, actor_cell, AccessDenied

STATUSES = {'registered': 'Зарегистрированная ячейка', 'administration_support': 'Поддержка администрации', 'forming': 'В процессе формирования'}

async def export_scope(session, user, region_id=None):
    if await actor_cell(session, user) is not None:
        raise AccessDenied('Выгрузка ячеек доступна руководителю региона и выше')
    ids = await accessible_region_ids(session, user)
    if region_id is not None:
        if region_id not in ids:
            raise AccessDenied('Нет доступа к этому региону')
        ids = [region_id]
    if not ids:
        raise AccessDenied('Нет доступных регионов')
    if len(ids) > 50:
        raise HTTPException(413, 'Выберите один регион для выгрузки')
    return ids

async def prepare_cells(session, user, region_id, mode, cell_id=None):
    ids = await export_scope(session, user, region_id)
    stmt = select(UniversityCell).where(UniversityCell.region_id.in_(ids), UniversityCell.is_active.is_(True))
    if cell_id is not None:
        stmt = stmt.where(UniversityCell.id == cell_id)
    cells = list((await session.execute(stmt.order_by(UniversityCell.region_id, UniversityCell.id).limit(2001))).scalars())
    if cell_id is not None and not cells:
        raise HTTPException(404, 'Ячейка недоступна')
    if len(cells) > 2000:
        raise HTTPException(413, 'Слишком много ячеек. Выберите один регион')
    cell_ids = [c.id for c in cells]
    # Region predicates also exclude inconsistent historical cross-region links.
    members = list((await session.execute(select(Member).where(Member.cell_id.in_(cell_ids), Member.region_id.in_(ids), Member.is_active.is_(True)).limit(20001))).scalars())
    if len(members) > 20000:
        raise HTTPException(413, 'Слишком большой состав. Выберите один регион')
    regions = {r.id: r.name for r in (await session.execute(select(Region).where(Region.id.in_(ids)))).scalars()}
    universities = {u.id: u.name for u in (await session.execute(select(University).where(University.id.in_([c.university_id for c in cells if c.university_id])))).scalars()}
    leaders = {u.id: u for u in (await session.execute(select(User).where(User.id.in_([c.leader_user_id for c in cells if c.leader_user_id])))).scalars()}
    leader_members = {m.id: m for m in (await session.execute(select(Member).where(Member.id.in_([u.member_id for u in leaders.values() if u.member_id])))).scalars()}
    rows, roster, finance, operations = [], [], [], []
    for c in cells:
        people = sorted([m for m in members if m.cell_id == c.id and m.region_id == c.region_id], key=lambda m: (m.full_name.casefold(), m.id))
        leader = leaders.get(c.leader_user_id)
        card = leader_members.get(leader.member_id) if leader else None
        rows.append([regions[c.region_id], universities.get(c.university_id, c.name), c.name,
                     leader.full_name if leader else '', (leader.phone or (card.phone if card else '')) if leader else '',
                     card.telegram_username if card else '', len(people), STATUSES.get(c.formation_status, 'Не указан')])
        if mode in ('members', 'all'):
            roster.extend([[regions[c.region_id], c.name, m.full_name, MEMBER_STATUS_LABELS.get(m.status, m.status)] for m in people])
    if mode in ('finance', 'all'):
        txs = list((await session.execute(select(Transaction).where(Transaction.cell_id.in_(cell_ids), Transaction.region_id.in_(ids)).order_by(Transaction.date, Transaction.id).limit(20001))).scalars())
        if len(txs) > 20000:
            raise HTTPException(413, 'Слишком много операций. Выберите один регион')
        for c in cells:
            own = [t for t in txs if t.cell_id == c.id and t.region_id == c.region_id]
            income = sum(t.amount for t in own if t.type == 'income')
            expense = sum(t.amount for t in own if t.type == 'expense')
            rub = lambda x: Decimal(x) / 100
            finance.append([regions[c.region_id], c.name, rub(c.allocated_budget) if c.allocated_budget is not None else None, rub(income), rub(expense), rub(income-expense)])
            operations.extend([[regions[c.region_id], c.name, t.date, 'Доход' if t.type == 'income' else 'Расход', rub(t.amount), t.comment or ''] for t in own])
    return await asyncio.to_thread(build_workbook, rows, roster, finance, operations, mode), ids

def build_workbook(rows, roster, finance, operations, mode):
    wb = Workbook(); wb.remove(wb.active)
    sheets = [('Ячейки', ['Регион', 'Название вуза', 'Ячейка', 'Руководитель', 'Телефон руководителя', 'Telegram руководителя', 'Численность', 'Статус'], rows)]
    if mode in ('members', 'all'):
        sheets.append(('Состав', ['Регион', 'Ячейка', 'ФИО', 'Статус участника'], roster))
    if mode in ('finance', 'all'):
        sheets.extend([('Финансы', ['Регион', 'Ячейка', 'Выделено, ₽', 'Доходы, ₽', 'Расходы, ₽', 'Баланс операций, ₽'], finance), ('Операции', ['Регион', 'Ячейка', 'Дата', 'Тип', 'Сумма, ₽', 'Комментарий'], operations)])
    sheets.append(('О выгрузке', ['Условие', 'Значение'], [['Численность', 'Активные участники, прикреплённые к ячейке'], ['Ячейки', 'Все активные, включая пустые и формирующиеся'], ['Финансы', 'За всё время; только операции с явной привязкой к ячейке. Общие операции региона не распределяются.'], ['Статус', 'Не указан — состояние ещё не заполнено руководителем']]))
    for name, headers, values in sheets:
        ws = wb.create_sheet(name); ws.append(headers)
        for row in values: ws.append(row)
        for row in ws:
            for cell in row:
                # Explicit strings cannot become formulas or external hyperlinks.
                if isinstance(cell.value, str): cell.data_type = 's'
                if isinstance(cell.value, Decimal): cell.number_format = '#,##0.00'
        for cell in ws[1]: cell.font = Font(bold=True, color='FFFFFF'); cell.fill = PatternFill('solid', fgColor='263238')
        ws.freeze_panes = 'A2'; ws.auto_filter.ref = ws.dimensions
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = min(48, max(16, max(len(str(c.value or '')) for c in col)+2))
    out = BytesIO(); wb.save(out); return out.getvalue()
