import csv
import io
from datetime import date

from openpyxl import load_workbook

from services.reports import RegionReport
from utils.csv_export import build_transactions_csv
from utils.report_export import build_region_pdf, build_region_xlsx


def _report(value: str) -> RegionReport:
    return RegionReport(
        region_name=value,
        period_label="Месяц",
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
        balance=0,
        income=0,
        expense=0,
        members=[(value, "Активист", "", "", "", "", "", "", "")],
        transactions=[(date(2026, 1, 1), "Расход", "Категория", "", 100, value, "Автор")],
    )


def test_csv_neutralizes_spreadsheet_formulas():
    payload = build_transactions_csv(
        [(date(2026, 1, 1), "expense", "Категория", None, 100, '=HYPERLINK("https://attacker.invalid")', "Автор")]
    ).decode("utf-8-sig")
    row = list(csv.reader(io.StringIO(payload), delimiter=";"))[1]
    assert row[5].startswith("'=")


def test_xlsx_stores_external_text_as_text_not_formula():
    payload = build_region_xlsx(_report("=1+1"))
    workbook = load_workbook(io.BytesIO(payload), data_only=False)
    assert workbook["Состав"]["A2"].data_type == "s"
    assert workbook["Состав"]["A2"].value == "'=1+1"


def test_pdf_escapes_reportlab_markup():
    payload = build_region_pdf(_report("<b>внешний текст</b>"))
    assert payload.startswith(b"%PDF")
