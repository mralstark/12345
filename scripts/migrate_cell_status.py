"""Add cell formation status without guessing the state of historical cells."""
import asyncio
from sqlalchemy import inspect, text
from database.db import engine

async def migrate():
    async with engine.begin() as conn:
        names = await conn.run_sync(lambda c: {x['name'] for x in inspect(c).get_columns('university_cells')})
        if 'formation_status' not in names:
            await conn.execute(text('ALTER TABLE university_cells ADD COLUMN formation_status VARCHAR(32)'))

if __name__ == '__main__':
    asyncio.run(migrate())
