"""Repeatable additive migration for browser login and event headcounts."""
import asyncio

from sqlalchemy import inspect, text

from database.db import engine
from database.models import Base, BrowserSession


async def migrate() -> None:
    async with engine.begin() as conn:
        columns = await conn.run_sync(lambda c: {x["name"] for x in inspect(c).get_columns("events")})
        if "total_attendees" not in columns:
            await conn.execute(text("ALTER TABLE events ADD COLUMN total_attendees INTEGER "
                                    "CHECK (total_attendees >= 0 AND total_attendees <= 1000000)"))
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=[BrowserSession.__table__]))
        tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
        # Additive acknowledgements; existing aggregate flags retain their meaning.
        for table, additions in {
            "tasks": {
                "overdue_assignee_notified": "BOOLEAN NOT NULL DEFAULT FALSE",
                "overdue_author_notified": "BOOLEAN NOT NULL DEFAULT FALSE",
            },
            "event_attendance": {"reminder_sent_on": "DATE"},
        }.items():
            if table not in tables:
                continue
            existing = await conn.run_sync(lambda c: {x["name"] for x in inspect(c).get_columns(table)})
            for column, definition in additions.items():
                if column not in existing:
                    await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))
    print("Service reliability schema ready")


if __name__ == "__main__":
    asyncio.run(migrate())
