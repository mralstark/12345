from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from config import APP_TIMEZONE

_TZ = ZoneInfo(APP_TIMEZONE)


def today() -> date:
    """Текущая дата в часовом поясе приложения (APP_TIMEZONE), а не сервера."""
    return datetime.now(_TZ).date()


def now() -> datetime:
    """Текущее время в часовом поясе приложения (APP_TIMEZONE), а не сервера."""
    return datetime.now(_TZ)


def iso_utc(value: datetime | None) -> str | None:
    """Момент времени для клиента — с пометкой часового пояса.

    База пишет created_at через func.now(), сервер живёт по UTC, и в колонке
    лежит время без пояса. Без пометки браузер читает такую строку как своё
    местное время: пост, выставленный в Москве в 17:06, показывался как 14:06.

    Пометив время как всемирное, отдаём разбираться браузеру — он переведёт
    его в пояс читателя. Это важнее, чем показывать всем APP_TIMEZONE: у
    Братства отделения от Петербурга до Красноярска, и «сегодня в 19:00»
    должно значить девять вечера у того, кто это читает.

    Только для колонок DateTime. Даты (Date) сдвигать нельзя: день рождения
    и день мероприятия — не моменты времени, у них пояса нет.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()
