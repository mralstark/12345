"""Добавляет ограничения целостности, введённые при усилении безопасности.

Запускать после обновления кода и до перезапуска сервисов:

    python -m scripts.migrate_security_constraints

Скрипт идемпотентен. Он не исправляет сомнительные данные автоматически:
если найдены отрицательные звёзды или повторная покупка одного товара,
миграция останавливается и сообщает причину.
"""

import asyncio

from sqlalchemy import inspect, text

from database.db import engine, init_db


async def migrate() -> None:
    # На чистой установке сначала создаются таблицы уже с новыми ограничениями.
    await init_db()

    async with engine.begin() as conn:
        dialect = conn.dialect.name
        if dialect != "postgresql":
            print(f"Ограничения уже входят в схему create_all ({dialect}); миграция не требуется")
            return

        tables = await conn.run_sync(lambda sync_conn: set(inspect(sync_conn).get_table_names()))
        if not {"members", "shop_purchases"}.issubset(tables):
            raise RuntimeError("Не найдены таблицы members и shop_purchases")

        negative = await conn.scalar(text("SELECT count(*) FROM members WHERE stars < 0"))
        duplicate = (
            await conn.execute(
                text(
                    "SELECT member_id, item_id, count(*) AS copies "
                    "FROM shop_purchases GROUP BY member_id, item_id "
                    "HAVING count(*) > 1 LIMIT 1"
                )
            )
        ).first()
        if negative:
            raise RuntimeError(f"Найдено записей members с отрицательными stars: {negative}")
        if duplicate:
            raise RuntimeError(
                "Найдена повторная покупка: "
                f"member_id={duplicate.member_id}, item_id={duplicate.item_id}, copies={duplicate.copies}"
            )

        await conn.execute(
            text(
                """
                DO $$ BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'ck_members_stars_nonnegative'
                          AND conrelid = 'members'::regclass
                    ) THEN
                        ALTER TABLE members
                        ADD CONSTRAINT ck_members_stars_nonnegative CHECK (stars >= 0);
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'uq_shop_purchase_member_item'
                          AND conrelid = 'shop_purchases'::regclass
                    ) THEN
                        ALTER TABLE shop_purchases
                        ADD CONSTRAINT uq_shop_purchase_member_item UNIQUE (member_id, item_id);
                    END IF;
                END $$;
                """
            )
        )
        print("Ограничения безопасности базы данных применены")


if __name__ == "__main__":
    asyncio.run(migrate())
