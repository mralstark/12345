"""Миграция: таблица membership_applications + regions.application_code
(публичная саморегистрация с подтверждением руководителем).

    python -m scripts.migrate_membership_applications
"""

import asyncio

from sqlalchemy import inspect, select, text

from database.db import async_session, engine
from database.models import Base, MembershipApplication, Region
from utils.invites import generate_application_code


async def migrate() -> None:
    async with engine.begin() as conn:
        has_table = await conn.run_sync(lambda sync_conn: inspect(sync_conn).has_table("membership_applications"))
        if not has_table:
            await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=[MembershipApplication.__table__]))
            print("Создана таблица membership_applications")
        else:
            print("Таблица membership_applications уже есть")

        has_regions = await conn.run_sync(lambda sync_conn: inspect(sync_conn).has_table("regions"))
        if not has_regions:
            print("Таблица regions ещё не создана — колонка появится вместе с ней.")
            return

        existing_cols = await conn.run_sync(lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("regions")})
        if "application_code" not in existing_cols:
            await conn.execute(text("ALTER TABLE regions ADD COLUMN application_code VARCHAR(32)"))
            print("Добавлена колонка regions.application_code (VARCHAR(32))")
        else:
            print("Колонка regions.application_code уже есть")

    # Бэкофилл: у существующих регионов ещё нет кода — без него не работает
    # многоразовая ссылка на вступление.
    async with async_session() as session:
        regions = list((await session.execute(select(Region).where(Region.application_code.is_(None)))).scalars().all())
        for region in regions:
            region.application_code = generate_application_code()
            print(f"Регион «{region.name}»: код заявки {region.application_code}")
        if regions:
            await session.commit()
        else:
            print("У всех регионов уже есть код заявки — бэкофилл не нужен.")


if __name__ == "__main__":
    asyncio.run(migrate())
