"""Excel mirrors the allocation ledger and never posts regional income."""
from decimal import Decimal
from io import BytesIO
import re
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill


def excel_value(value):
    # Legacy names may predate input validation. Preserve a visible replacement
    # rather than letting an XML-illegal character break the entire workbook.
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]', '\ufffd', value) if isinstance(value,str) else value


def workbook(overview, entries):
    book=Workbook();book.remove(book.active)
    rows=overview['items']
    sheets=[('Планы',['Регион / отдел','Месяц','План, ₽','Основание плана'],[
        [r['name'],overview['month'],Decimal(r['plan']['amount'])/100 if r['plan'] else None,r['plan']['note'] if r['plan'] else 'План не задан'] for r in rows]),
        ('Выдачи',['Дата','Регион / отдел','Сумма, ₽','Тип записи','Комментарий','Автор','Номер записи','Отменяет запись'],[
         [e['date'],e['region_name'],Decimal(e['amount'] if e['kind']=='issue' else -e['amount'])/100,'Отмена' if e['kind']=='cancel' else ('Выдача отменена' if e['cancelled'] else 'Выдача'),e['comment'],e['actor'],e['id'],e['cancels_id']] for e in entries]),
        ('Сводка',['Регион / отдел','План, ₽','Выдано за вычетом отмен, ₽','Осталось выдать, ₽','Сверх плана, ₽'],[
         [r['name'],Decimal(r['plan']['amount'])/100 if r['plan'] else None,Decimal(r['issued'])/100,Decimal(r['remaining'])/100 if r['remaining'] is not None else None,Decimal(max(0,-r['remaining']))/100 if r['remaining'] is not None else None] for r in rows]),
        ('Условия',['Параметр','Описание'],[['Период плана',overview['month']],['Реестр','Это учёт финансирования, а не доходы и расходы региона.'],['Отмены','Исходная запись и отмена сохранены. Сводка исключает отменённые выдачи.'],['Бюджет отдела','Самостоятельный бюджет, не источник сумм регионов.'],['Пустой план','План ещё не задан. Нулевой план отображается числом 0.']])]
    for name,headers,values in sheets:
        ws=book.create_sheet(name);ws.append(headers)
        for row in values:ws.append([excel_value(value) for value in row])
        for row in ws:
            for cell in row:
                if isinstance(cell.value,str):cell.data_type='s'
                if isinstance(cell.value,Decimal):cell.number_format='#,##0.00'
        for cell in ws[1]:cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='292929')
        ws.freeze_panes='A2';ws.auto_filter.ref=ws.dimensions
        for col in ws.columns:ws.column_dimensions[col[0].column_letter].width=min(48,max(18,max(len(str(c.value or '')) for c in col)+2))
    out=BytesIO();book.save(out);return out.getvalue()
