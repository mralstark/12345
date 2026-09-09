"""Миграция: каталог University + members.university_id, university_cells.university_id.

Переносит старые свободнотекстовые значения members.university (если колонка
ещё существует) в каталог без потери данных: каждое различное значение (без
учёта регистра и пробелов по краям) становится одной записью University, и
все совпавшие Member получают её id. Колонку members.university физически
не удаляем — ей просто больше никто не пользуется (API и веб уже на
university_id).

    python -m scripts.migrate_university_catalog
"""

import asyncio

from sqlalchemy import inspect, select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from database.db import async_session, engine
from database.models import Base, University


async def _add_column_if_missing(conn: AsyncConnection, table: str, column: str, ddl: str) -> bool:
    has_table = await conn.run_sync(lambda c: inspect(c).has_table(table))
    if not has_table:
        return False
    existing = await conn.run_sync(lambda c: {col["name"] for col in inspect(c).get_columns(table)})
    if column in existing:
        return False
    await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
    return True


async def migrate() -> None:
    legacy_present = False

    async with engine.begin() as conn:
        has_universities = await conn.run_sync(lambda c: inspect(c).has_table("universities"))
        if not has_universities:
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=[University.__table__]))
            print("Создана таблица universities")
        else:
            print("Таблица universities уже есть")

        if await _add_column_if_missing(conn, "members", "university_id", "INTEGER"):
            print("Добавлена колонка members.university_id")
        else:
            print("members.university_id уже есть (или таблицы members ещё нет)")

        if await _add_column_if_missing(conn, "university_cells", "university_id", "INTEGER"):
            print("Добавлена колонка university_cells.university_id")
        else:
            print("university_cells.university_id уже есть (или таблицы ещё нет)")

        has_members = await conn.run_sync(lambda c: inspect(c).has_table("members"))
        if has_members:
            member_cols = await conn.run_sync(lambda c: {col["name"] for col in inspect(c).get_columns("members")})
            legacy_present = "university" in member_cols

    if not legacy_present:
        print("Старой текстовой колонки members.university нет — переносить нечего.")
        return

    async with async_session() as session:
        # SQLite lower() не приводит кириллицу к нижнему регистру (тот же
        # нюанс, что и в api/routers/members.py::list_members) — поэтому и
        # сопоставление с уже существующим каталогом, и дедупликация внутри
        # самого переноса целиком на Python-стороне, без func.lower() в SQL.
        by_normalized: dict[str, University] = {
            u.name.lower(): u for u in (await session.execute(select(University))).scalars().all()
        }

        rows = (
            await session.execute(
                text("SELECT id, university FROM members WHERE university IS NOT NULL AND trim(university) <> ''")
            )
        ).all()

        created = 0
        matched = 0
        for member_id, raw in rows:
            name = raw.strip()
            if not name:
                continue
            key = name.lower()

            university = by_normalized.get(key)
            if university is None:
                university = University(name=name)
                session.add(university)
                await session.flush()
                by_normalized[key] = university
                created += 1

            await session.execute(
                text("UPDATE members SET university_id = :uid WHERE id = :member_id"),
                {"uid": university.id, "member_id": member_id},
            )
            matched += 1

        await session.commit()

    print(f"Перенесено: {created} новых записей в каталог, {matched} человек получили university_id")


if __name__ == "__main__":
    asyncio.run(migrate())
