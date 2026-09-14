"""Безопасное преобразование пользовательских значений для отчётов."""

from html import escape

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")


def spreadsheet_cell(value):
    """Не даёт Excel/LibreOffice интерпретировать внешний текст как формулу."""
    if not isinstance(value, str):
        return value
    candidate = value.lstrip(" \t\r\n")
    if value.startswith(_FORMULA_PREFIXES) or candidate.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def reportlab_text(value) -> str:
    """Экранирует mini-markup ReportLab в данных отчёта."""
    return escape(str(value), quote=True)
