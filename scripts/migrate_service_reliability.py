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
    print("Service reliability schema ready")


if __name__ == "__main__":
    asyncio.run(migrate())
