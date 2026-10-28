"""Фоновые уведомления (ТЗ §14): дни рождения, приближение дедлайна, просрочка.

Крутится в процессе бота. Все проверки идемпотентны по дню — перезапуск бота
не приводит к повторной отправке (BirthdayNotice, deadline_notified_on,
overdue_notified). Заодно раз в цикл досоздаются повторяющиеся мероприятия.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from datetime import time as time_

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from database.db import async_session
from database.models import (
    EVENT_STATUS_PLANNED,
    TASK_STATUSES_OPEN,
    BirthdayNotice,
    Event,
    EventAttendance,
    Member,
    Region,
    Task,
    User,
)
from services.education import bump_courses
from services.recurring_events import materialize_all
from utils.notify import escape_telegram_html, notify_telegram
from utils.parser import first_and_patronymic, format_date_ru
from utils.tz import now as tz_now
from utils.tz import today as tz_today

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 15 * 60
# За сколько дней до срока напоминать исполнителю.
DEADLINE_WARNING_DAYS = 1
# За сколько напоминать о мероприятии тем, кто отметился «иду».
EVENT_REMINDER_LEAD = timedelta(hours=24)


async def check_birthdays(bot: Bot) -> int:
    """Уведомление руководителю в сам день рождения члена состава (ТЗ §6)."""
    today = tz_today()
    sent = 0

    async with async_session() as session:
        result = await session.execute(
            select(Member, Region, User)
            .join(Region, Region.id == Member.region_id)
            .join(User, User.id == Region.leader_user_id)
            .where(Member.is_active.is_(True), Member.birth_date.is_not(None))
        )
        # Забираем поля сразу в обычные значения: ниже возможен rollback (уже
        # отправляли сегодня), а он разворачивает все ORM-объекты сессии — и
        # следующее обращение к атрибуту ушло бы в ленивую подгрузку из async-кода.
        birthdays = [
            (member.id, member.full_name, member.phone, member.birth_date, region.name, leader.telegram_id)
            for member, region, leader in result.all()
            if member.birth_date.month == today.month and member.birth_date.day == today.day
        ]

        for member_id, full_name, phone, birth_date, region_name, leader_telegram_id in birthdays:
            existing = (await session.execute(select(BirthdayNotice.id).where(
                BirthdayNotice.member_id == member_id, BirthdayNotice.year == today.year,
            ))).scalar_one_or_none()
            if existing is not None:
                continue
            session.add(BirthdayNotice(member_id=member_id, year=today.year))
            try:
                # Claim inside the transaction, but do not commit a sent marker
                # until Telegram confirms it. A competing worker waits here.
                await session.flush()
            except IntegrityError:
                await session.rollback()
                continue

            age = today.year - birth_date.year
            receipt = await notify_telegram(
                leader_telegram_id,
                f"🎂 <b>День рождения</b>\n\n"
                f"Сегодня {age} лет — {escape_telegram_html(full_name)} "
                f"({escape_telegram_html(region_name)}).\n"
                f"{escape_telegram_html('Телефон: ' + phone) if phone else ''}",
                bot=bot,
            )
            if receipt:
                await session.commit()
                sent += 1
            else:
                await session.rollback()

    return sent


async def check_deadlines(bot: Bot) -> int:
    """Напоминание исполнителю о приближающемся сроке."""
    today = tz_today()
    threshold = today + timedelta(days=DEADLINE_WARNING_DAYS)
    sent = 0

    async with async_session() as session:
        result = await session.execute(
            select(Task, User)
            .join(User, User.id == Task.to_user_id)
            .where(
                Task.status.in_(TASK_STATUSES_OPEN),
                Task.deadline.is_not(None),
                Task.deadline <= threshold,
                Task.deadline >= today,
            )
        )
        for task, assignee in result.all():
            if task.deadline_notified_on == today:
                continue
            when = "сегодня" if task.deadline == today else format_date_ru(task.deadline)
            receipt = await notify_telegram(
                assignee.telegram_id,
                f"⏰ <b>Приближается срок</b>\n\n«{escape_telegram_html(task.title)}» — "
                f"до {escape_telegram_html(when)}.",
                bot=bot,
            )
            if receipt:
                task.deadline_notified_on = today
                await session.commit()
                sent += 1

    return sent


async def check_overdue(bot: Bot) -> int:
    """Просрочка — уведомление и исполнителю, и постановщику (ТЗ §14)."""
    today = tz_today()
    sent = 0

    async with async_session() as session:
        result = await session.execute(
            select(Task).where(
                Task.status.in_(TASK_STATUSES_OPEN),
                Task.deadline.is_not(None),
                Task.deadline < today,
                Task.overdue_notified.is_(False),
            )
        )
        for task in result.scalars().all():
            assignee = await session.get(User, task.to_user_id)
            author = await session.get(User, task.from_user_id)
            text = (
                f"🔴 <b>Задача просрочена</b>\n\n"
                f"«{escape_telegram_html(task.title)}»\nСрок был: {format_date_ru(task.deadline)}"
            )
            if not task.overdue_assignee_notified:
                if assignee and assignee.telegram_id:
                    receipt = await notify_telegram(assignee.telegram_id, text, bot=bot)
                    if receipt:
                        task.overdue_assignee_notified = True
                        sent += 1
                else:
                    task.overdue_assignee_notified = True  # No Telegram recipient.
                await session.commit()
            if not task.overdue_author_notified:
                if author and author.telegram_id and author.id != task.to_user_id:
                    receipt = await notify_telegram(
                        author.telegram_id,
                        text + f"\nИсполнитель: {escape_telegram_html(assignee.full_name if assignee else '—')}",
                        bot=bot,
                    )
                    if receipt:
                        task.overdue_author_notified = True
                        sent += 1
                elif author and author.id == task.to_user_id:
                    task.overdue_author_notified = task.overdue_assignee_notified
                else:
                    task.overdue_author_notified = True
            task.overdue_notified = task.overdue_assignee_notified and task.overdue_author_notified
            await session.commit()

    return sent


async def check_event_reminders(bot: Bot) -> int:
    """Напоминание за 24 часа тем, кто отметился «иду» (RSVP) — по имени-отчеству,
    как и приветствие в личном кабинете (utils.notify.send_cabinet_welcome).

    Момент мероприятия — date+time (без time — полночь начала дня, тогда
    напоминание уходит от полуночи дня накануне). reminder_sent_on остаётся
    идемпотентностью «на сегодня уже слали» (цикл крутится каждые 15 минут,
    порог «ровно 24 часа» проверяется точным datetime ниже, не самой датой)."""
    now = tz_now()
    today = now.date()
    sent = 0

    async with async_session() as session:
        # Порог «за 24 часа» может попасть и на сегодня, и на завтра —
        # берём оба дня, точное решение даёт starts_at ниже.
        events = (
            await session.execute(
                select(Event).where(
                    Event.date.in_([today, today + timedelta(days=1)]),
                    Event.status == EVENT_STATUS_PLANNED,
                )
            )
        ).scalars().all()

        for event in events:
            if event.reminder_sent_on == today:
                continue
            starts_at = datetime.combine(event.date, event.time or time_(0, 0), tzinfo=now.tzinfo)
            if starts_at - EVENT_REMINDER_LEAD > now or starts_at <= now:
                continue
            when = format_date_ru(event.date) + (event.time.strftime(', %H:%M') if event.time else '')
            attendees = await session.execute(
                select(EventAttendance, User.telegram_id, User.full_name)
                .select_from(EventAttendance)
                .join(Member, Member.id == EventAttendance.member_id)
                .join(User, User.member_id == Member.id)
                .where(EventAttendance.event_id == event.id, EventAttendance.attended.is_(True))
            )
            all_delivered = True
            for attendance, telegram_id, full_name in attendees.all():
                if not telegram_id or attendance.reminder_sent_on == today:
                    continue
                receipt = await notify_telegram(
                    telegram_id,
                    f"📅 <b>Напоминание</b>\n\n{escape_telegram_html(first_and_patronymic(full_name))}, "
                    f"через 24 часа — «{escape_telegram_html(event.title)}» "
                    f"({escape_telegram_html(when)}). Вы отметились как «иду».",
                    bot=bot,
                )
                if receipt:
                    attendance.reminder_sent_on = today
                    await session.commit()
                    sent += 1
                else:
                    all_delivered = False
            if all_delivered:
                event.reminder_sent_on = today
                await session.commit()

    return sent


async def run_checks(bot: Bot) -> None:
    await check_birthdays(bot)
    await check_deadlines(bot)
    await check_overdue(bot)
    await check_event_reminders(bot)
    async with async_session() as session:
        await materialize_all(session)
        await bump_courses(session)


async def notifier_loop(bot: Bot) -> None:
    while True:
        try:
            await run_checks(bot)
        except Exception:  # noqa: BLE001 — цикл не должен умирать от единичной ошибки
            logger.exception("Ошибка в цикле уведомлений")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
