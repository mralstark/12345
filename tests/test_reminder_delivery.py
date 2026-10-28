from datetime import date, timedelta
from unittest.mock import AsyncMock

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from database.models import BirthdayNotice, Event, EventAttendance, Member, User
from services import notifier
from services.tasks import create_task
from utils.tz import now as tz_now, today as tz_today


async def test_failed_birthday_is_retried_and_only_success_is_acknowledged(session,world,monkeypatch):
    today=tz_today()
    session.add(Member(region_id=world["moscow"].id,full_name="Synthetic Birthday",birth_date=date(2000,today.month,today.day)))
    await session.commit()
    send=AsyncMock(side_effect=[None,True]);monkeypatch.setattr(notifier,"notify_telegram",send)
    assert await notifier.check_birthdays(None)==0
    assert (await session.scalar(select(func.count()).select_from(BirthdayNotice)))==0
    assert await notifier.check_birthdays(None)==1
    assert await notifier.check_birthdays(None)==0
    assert send.await_count==2


async def test_failed_deadline_is_not_marked_sent(session,world,monkeypatch):
    task=await create_task(session,world["coordinator"],world["leader_moscow"].id,"Synthetic deadline",deadline=tz_today())
    send=AsyncMock(side_effect=[None,True]);monkeypatch.setattr(notifier,"notify_telegram",send)
    assert await notifier.check_deadlines(None)==0
    await session.refresh(task);assert task.deadline_notified_on is None
    assert await notifier.check_deadlines(None)==1
    assert await notifier.check_deadlines(None)==0
    assert send.await_count==2


async def test_partial_overdue_delivery_retries_only_the_missing_recipient(session,world,monkeypatch):
    task=await create_task(session,world["coordinator"],world["leader_moscow"].id,"Synthetic overdue",deadline=tz_today()-timedelta(days=2))
    send=AsyncMock(side_effect=[True,None,True]);monkeypatch.setattr(notifier,"notify_telegram",send)
    assert await notifier.check_overdue(None)==1
    await session.refresh(task)
    assert task.overdue_assignee_notified and not task.overdue_author_notified and not task.overdue_notified
    assert await notifier.check_overdue(None)==1
    assert send.await_count==3 and send.await_args.args[0]==world["coordinator"].telegram_id
    assert await notifier.check_overdue(None)==0
    await session.refresh(task);assert task.overdue_notified


async def test_partial_event_delivery_does_not_repeat_successful_attendees(session,world,monkeypatch):
    moment=tz_now()+timedelta(hours=23)
    event=Event(region_id=world["moscow"].id,title="Synthetic reminder",date=moment.date(),time=moment.time())
    first=Member(region_id=world["moscow"].id,full_name="Synthetic first")
    second=Member(region_id=world["moscow"].id,full_name="Synthetic second")
    session.add_all([event,first,second]);await session.flush()
    session.add_all([User(full_name=first.full_name,role="participant",telegram_id=20001,member_id=first.id),
                     User(full_name=second.full_name,role="participant",telegram_id=20002,member_id=second.id),
                     EventAttendance(event_id=event.id,member_id=first.id),EventAttendance(event_id=event.id,member_id=second.id)])
    await session.commit()
    calls=[]
    async def send(recipient,*args,**kwargs):
        calls.append(recipient)
        return None if recipient==20002 and calls.count(recipient)==1 else True
    monkeypatch.setattr(notifier,"notify_telegram",send)
    assert await notifier.check_event_reminders(None)==1
    await session.refresh(event);assert event.reminder_sent_on is None
    assert await notifier.check_event_reminders(None)==1
    assert calls.count(20001)==1 and calls.count(20002)==2
    assert await notifier.check_event_reminders(None)==0
    await session.refresh(event);assert event.reminder_sent_on==tz_today()


async def test_legacy_delivery_migration_is_repeatable_and_does_not_reset_old_flags(tmp_path,monkeypatch):
    from scripts import migrate_service_reliability
    engine=create_async_engine(f"sqlite+aiosqlite:///{(tmp_path/'legacy.db').as_posix()}")
    monkeypatch.setattr(migrate_service_reliability,"engine",engine)
    try:
        async with engine.begin() as db:
            await db.execute(text("CREATE TABLE events (id INTEGER PRIMARY KEY)"))
            await db.execute(text("CREATE TABLE tasks (id INTEGER PRIMARY KEY, overdue_notified BOOLEAN NOT NULL)"))
            await db.execute(text("INSERT INTO tasks VALUES (1,TRUE)"))
            await db.execute(text("CREATE TABLE event_attendance (id INTEGER PRIMARY KEY)"))
            await db.execute(text("INSERT INTO event_attendance VALUES (1)"))
        await migrate_service_reliability.migrate()
        await migrate_service_reliability.migrate()
        async with engine.connect() as db:
            row=(await db.execute(text("SELECT overdue_notified,overdue_assignee_notified,overdue_author_notified FROM tasks WHERE id=1"))).one()
            assert tuple(row)==(1,0,0)
            assert (await db.execute(text("SELECT reminder_sent_on FROM event_attendance WHERE id=1"))).scalar_one() is None
    finally:await engine.dispose()
