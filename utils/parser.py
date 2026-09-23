"""Разбор дат текстом и форматирование денег."""

import re
from datetime import date, timedelta

_DATE_DDMM_RE = re.compile(r"^(?P<day>\d{1,2})\.(?P<month>\d{1,2})(?:\.(?P<year>\d{2,4}))?$")
_DATE_DAY_ONLY_RE = re.compile(r"^(?P<day>\d{1,2})$")


def parse_date_hint(text: str, today: date) -> date | None:
    """Разбирает «21», «вчера», «26.06» в дату не позже today."""
    normalized = text.strip().lower()

    if normalized == "вчера":
        return today - timedelta(days=1)
    if normalized == "сегодня":
        return today

    match = _DATE_DDMM_RE.match(normalized)
    if match:
        day = int(match.group("day"))
        month = int(match.group("month"))
        year_text = match.group("year")

        if year_text:
            year = int(year_text)
            if year < 100:
                year += 2000
            try:
                return date(year, month, day)
            except ValueError:
                return None

        try:
            candidate = date(today.year, month, day)
        except ValueError:
            return None
        if candidate > today:
            try:
                candidate = date(today.year - 1, month, day)
            except ValueError:
                return None
        return candidate

    match = _DATE_DAY_ONLY_RE.match(normalized)
    if match:
        day = int(match.group("day"))
        try:
            candidate = date(today.year, today.month, day)
        except ValueError:
            return None
        if candidate > today:
            prev_month = today.month - 1 or 12
            prev_year = today.year if today.month > 1 else today.year - 1
            try:
                candidate = date(prev_year, prev_month, day)
            except ValueError:
                return None
        return candidate

    return None


def parse_deadline(text: str, today: date) -> date | None:
    """Дедлайн задачи: «31.12», «31.12.2026», «завтра». В отличие от parse_date_hint
    смотрит вперёд — «26.06» без года означает ближайшее будущее, а не прошлое."""
    normalized = text.strip().lower()

    if normalized in ("сегодня",):
        return today
    if normalized in ("завтра",):
        return today + timedelta(days=1)

    match = _DATE_DDMM_RE.match(normalized)
    if match is None:
        return None

    day = int(match.group("day"))
    month = int(match.group("month"))
    year_text = match.group("year")

    if year_text:
        year = int(year_text)
        if year < 100:
            year += 2000
        try:
            return date(year, month, day)
        except ValueError:
            return None

    try:
        candidate = date(today.year, month, day)
    except ValueError:
        return None
    if candidate < today:
        try:
            candidate = date(today.year + 1, month, day)
        except ValueError:
            return None
    return candidate


def format_kopecks(amount_kopecks: int) -> str:
    sign = "-" if amount_kopecks < 0 else ""
    rubles, kopecks = divmod(abs(amount_kopecks), 100)
    rubles_str = f"{rubles:,}".replace(",", " ")
    if kopecks:
        return f"{sign}{rubles_str},{kopecks:02d}"
    return f"{sign}{rubles_str}"


def format_money(amount_kopecks: int) -> str:
    return f"{format_kopecks(amount_kopecks)} ₽"


_MONTHS_GENITIVE = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def format_date_ru(d: date) -> str:
    """«22 июля 2026»."""
    return f"{d.day} {_MONTHS_GENITIVE[d.month - 1]} {d.year}"


def normalize_keyword(word: str) -> str:
    return word.strip().lower()


TELEGRAM_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{2,31}$")


def normalize_phone(raw: str) -> str:
    """Приводит номер из Telegram/формы к E.164 без разделителей.

    Российскую восьмёрку заменяем на +7; десятизначный номер считаем
    российским. Остальные международные номера принимаем с 10–15 цифрами.
    """
    value = raw.strip()
    digits = re.sub(r"\D", "", value)
    if len(digits) == 10:
        digits = "7" + digits
    elif len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if not 10 <= len(digits) <= 15 or digits.startswith("0"):
        raise ValueError("Введите действующий номер телефона")
    return "+" + digits


def normalize_telegram_username(raw: str) -> str:
    """«qwerty» / «@qwerty» → «@qwerty», с проверкой формата (буквы/цифры/
    подчёркивание, начинается с буквы — как у настоящих ников Telegram).
    Бросает ValueError с человекочитаемым текстом при неверном формате —
    вызывающий сам решает, во что это завернуть (HTTPException и т.п.)."""
    value = raw.strip()
    if value.startswith("@"):
        value = value[1:]
    if not TELEGRAM_USERNAME_RE.match(value):
        raise ValueError('Ник в Telegram — латиницей, начинается с буквы, формат "@qwerty"')
    return "@" + value


def first_and_patronymic(full_name: str) -> str:
    """«Иванов Иван Иванович» → «Иван Иванович» — для личного приветствия
    (utils/notify.py::send_cabinet_welcome). ФИО в этом проекте всегда
    вводится в порядке Фамилия Имя Отчество (форма регистрации, «Состав»).
    Без отчества («Иванов Иван») — просто имя; одно слово — оно и есть."""
    parts = full_name.split()
    if len(parts) >= 3:
        return f"{parts[1]} {parts[2]}"
    if len(parts) == 2:
        return parts[1]
    return full_name
