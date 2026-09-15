"""Демо-инициализация должна соответствовать текущей схеме и быть повторяемой."""

from sqlalchemy import func, select

from database.db import async_session
from database.models import Event, Member, Region
from scripts.seed_demo import main as seed_demo


async def test_seed_demo_populates_empty_database_and_is_idempotent():
    await seed_demo()
    await seed_demo()

    async with async_session() as session:
        assert await session.scalar(select(func.count()).select_from(Region)) == 2
        assert await session.scalar(select(func.count()).select_from(Member)) == 29
        assert await session.scalar(select(func.count()).select_from(Event)) == 6
