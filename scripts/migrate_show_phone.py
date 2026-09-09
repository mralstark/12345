"""Миграция: show_contacts → show_phone.

Флаг перестал управлять контактами целиком. Ник в телеграме теперь виден
всегда — по нему открывается переписка, и ради этого профиль чаще всего и
открывают. Под выключателем остался только номер телефона.

Переименование, а не «добавить и удалить»: у кого контакты были открыты, у
того остаётся открытым и номер — прежний выбор человека переносится как есть.

    python -m scripts.migrate_show_phone
"""

import asyncio

from sqlalchemy import inspect, text

from database.db import engine


async def migrate() -> None:
    async with engine.begin() as conn:
        columns = await conn.run_sync(
            lambda c: {col["name"] for col in inspect(c).get_columns("members")}
        )
        if "show_phone" in columns:
            print("Колонка show_phone уже есть — миграция не нужна.")
            return
        if "show_contacts" not in columns:
            raise RuntimeError(
                "Нет ни show_contacts, ни show_phone — база не в том состоянии, "
                "которое эта миграция умеет переносить."
            )
        # RENAME COLUMN понимают и SQLite (с 3.25), и PostgreSQL — перестраивать
        # таблицу, как в migrate_news_open_feed, здесь не приходится.
        await conn.execute(text("ALTER TABLE members RENAME COLUMN show_contacts TO show_phone"))
        print("members.show_contacts переименована в members.show_phone")


if __name__ == "__main__":
    asyncio.run(migrate())
