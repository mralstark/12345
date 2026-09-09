from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import DATABASE_URL
from database.models import Base

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    """Создаёт недостающие таблицы. Аддитивно — вызывается при каждом старте бота и API.
    Изменение существующих таблиц (новые колонки) на боевой БД делается миграцией
    в scripts/, а не здесь: create_all такого не умеет."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        yield session
