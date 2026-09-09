"""Донаполнение: категория дохода «Бюджет» для регионов, у которых справочник
категорий уже был заполнен раньше (ensure_default_categories повторно не
досоздаёт список, если в нём уже что-то есть) — см. database/defaults.py.

    python -m scripts.migrate_add_budget_category
"""

import asyncio

from sqlalchemy import select

from database.db import async_session
from database.models import Category, Keyword, Region
from utils.parser import normalize_keyword


async def migrate() -> None:
    async with async_session() as session:
        regions = (await session.execute(select(Region))).scalars().all()
        added = 0
        for region in regions:
            existing = await session.execute(
                select(Category.id).where(Category.region_id == region.id, Category.name == "Бюджет")
            )
            if existing.scalar() is not None:
                continue
            category = Category(region_id=region.id, name="Бюджет", type="income", emoji="🏦")
            session.add(category)
            await session.flush()
            session.add(Keyword(category_id=category.id, word=normalize_keyword("бюджет")))
            added += 1
        await session.commit()
        print(f"Добавлена категория «Бюджет» в {added} регион(ов) из {len(regions)}")


if __name__ == "__main__":
    asyncio.run(migrate())
