import csv
import io
from collections.abc import Sequence

from utils.export_security import spreadsheet_cell
from utils.parser import format_kopecks


def build_transactions_csv(
    rows: Sequence[tuple],  # (дата, тип, категория|None, мероприятие|None, сумма_копейки, комментарий|None, автор|None)
) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")

    writer.writerow(["Дата", "Тип", "Категория", "Мероприятие", "Сумма", "Комментарий", "Внёс"])
    for tx_date, tx_type, category_name, event_title, amount, comment, author in rows:
        writer.writerow(
            [spreadsheet_cell(value) for value in [
                tx_date.isoformat(),
                "Доход" if tx_type == "income" else "Расход",
                category_name or "Без категории",
                event_title or "",
                format_kopecks(amount),
                comment or "",
                author or "",
            ]]
        )

    # BOM — иначе Excel открывает кириллицу кракозябрами.
    return ("﻿" + buffer.getvalue()).encode("utf-8")
