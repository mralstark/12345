"""Автообновление курса состава каждое 1 сентября.

Курс повышается сам; превышение длительности программы (study_years) только
обнуляет курс (человек больше не «на каком-то курсе» академически) — статус
«Выпускник» это НЕ трогает: это отдельное, осознанное решение руководителя
(«Выпуск из студенческого Братства» — событие в самом Братстве, не в вузе),
которое можно принять в любой момент, раньше или позже реального окончания
учёбы, см. api/routers/members.py."""

import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import MEMBER_STATUS_ALUMNI, Member
from utils.tz import today as tz_today

logger = logging.getLogger(__name__)


def academic_year(today: date) -> int:
    """Учебный год, которому принадлежит дата: считаем с 1 сентября.

    Публичная — используется и здесь, и в api/routers/members.py: там при
    создании/правке курса нужно сразу проставить course_bumped_year текущим
    учебным годом. Без этого свежедобавленного студента подхватил бы ближайший
    же цикл нотификатора и повысил курс, не дожидаясь настоящего 1 сентября.
    """
    return today.year if (today.month, today.day) >= (9, 1) else today.year - 1


async def bump_courses(session: AsyncSession, today: date | None = None) -> int:
    """Идемпотентна в пределах учебного года.

    В отличие от проверки дней рождения (check_birthdays), которая сверяет
    точный день и просто не отправит уведомление, если бот не работал именно
    1 сентября, здесь пропуск обошёлся бы дороже — целая когорта зависла бы
    на курсе на год. Поэтому сравниваем не «сегодня ровно 1 сентября», а
    «учебный год уже наступил, а отметка о повышении на этот год ещё нет» —
    сработает в любой день после 1 сентября, даже если бот был выключен
    именно в этот момент.
    """
    today = today or tz_today()
    year = academic_year(today)
    bumped = 0

    result = await session.execute(
        select(Member).where(
            Member.is_active.is_(True),
            Member.course.is_not(None),
            Member.status != MEMBER_STATUS_ALUMNI,
        )
    )
    for member in result.scalars().all():
        if (member.course_bumped_year or 0) >= year:
            continue
        member.course += 1
        member.course_bumped_year = year
        if member.course > member.study_years:
            # Только курс — статус «Выпускник» отдельно и вручную (см. docstring модуля).
            member.course = None
        bumped += 1

    if bumped:
        await session.commit()
        logger.info("Автообновление курса: изменено записей — %s", bumped)
    return bumped
