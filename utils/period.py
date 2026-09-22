"""Отчётные периоды — календарные (ТЗ §7: period_start_day бота-донора не переносится)."""

import calendar
from datetime import date

from utils.tz import today as tz_today

MONTH_NAMES = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]

PERIOD_KINDS = ("month", "semester", "year", "all")


def month_bounds(year: int, month: int) -> tuple[date, date]:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def semester_bounds(year: int, semester: int) -> tuple[date, date]:
    """Границы семестра; ``year`` — год начала учебного года."""
    if semester == 1:
        return date(year, 9, 1), date(year, 12, 31)
    return date(year + 1, 1, 1), date(year + 1, 6, 30)


def resolve_period(kind: str, offset: int = 0, today: date | None = None) -> tuple[date, date, str]:
    """Границы отчётного периода и его человекочитаемое название.
    offset — смещение назад в периодах: 0 — текущий, -1 — предыдущий."""
    if offset < -1200 or offset > 1200:
        raise ValueError("Смещение периода должно быть от -1200 до 1200")
    today = today or tz_today()

    if kind == "month":
        total = today.year * 12 + (today.month - 1) + offset
        year, month = divmod(total, 12)
        month += 1
        start, end = month_bounds(year, month)
        return start, end, f"{MONTH_NAMES[month - 1]} {year}"

    if kind == "semester":
        # Сентябрь–декабрь — первый семестр, январь–июнь — второй.
        # Июль и август относятся к завершившемуся второму семестру, чтобы
        # летняя аналитика не перескакивала вперёд до начала учебного года.
        current_semester = 1 if today.month >= 9 else 2
        academic_year = today.year if current_semester == 1 else today.year - 1
        total = academic_year * 2 + (current_semester - 1) + offset
        year, semester = divmod(total, 2)
        semester += 1
        start, end = semester_bounds(year, semester)
        return start, end, f"{semester} семестр {year}/{str(year + 1)[-2:]}"

    if kind == "year":
        year = today.year + offset
        return date(year, 1, 1), date(year, 12, 31), f"{year} год"

    if kind == "all":
        return date(2000, 1, 1), date(2100, 12, 31), "За всё время"

    raise ValueError(f"Неизвестный период: {kind}")
