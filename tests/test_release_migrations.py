"""Run release migrations, with the required offline upgrade for legacy encryption."""
import importlib
from pathlib import Path
import re

import pytest
from sqlalchemy import BigInteger, Date, MetaData, Text, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import Base, Member, MemberQuestProgress, NewsPost, Quest, Region, User
from database.encrypted import EncryptedValue
from scripts.data_encryption import migrate_database
from utils.data_crypto import DataEncryptionError


@pytest.mark.parametrize('script', ['deploy/update_vps.py','deploy/deploy.sh','deploy/deploy.ps1','deploy/setup_server.sh'])
async def test_deployment_upgrades_old_schema_before_catalog_orm(tmp_path, monkeypatch, script):
    source=(Path(__file__).resolve().parents[1]/script).read_text(encoding='utf-8')
    if script.endswith('update_vps.py'):
        sequence=re.search(r'for migration in ([^;]+); do',source).group(1).split()
    else:
        sequence=re.findall(r'-m scripts\.(migrate_[a-z_]+)',source)
    assert sequence
    old=create_async_engine(f"sqlite+aiosqlite:///{(tmp_path/'legacy.db').as_posix()}")
    factory=async_sessionmaker(old,expire_on_commit=False)
    try:
        legacy = MetaData()
        for table in Base.metadata.tables.values():
            table.to_metadata(legacy)
        for table in legacy.tables.values():
            for column in table.columns:
                if isinstance(column.type, EncryptedValue):
                    column.type = BigInteger() if column.type.kind == 'int' else Date() if column.type.kind == 'date' else Text()
        async with old.begin() as conn:
            await conn.run_sync(legacy.create_all)
            await conn.execute(legacy.tables['regions'].insert().values(id=1,name='Сохранённый регион'))
            await conn.execute(legacy.tables['users'].insert().values(id=1,full_name='Сохранённый автор',role='federal'))
            await conn.execute(legacy.tables['members'].insert().values(id=1,region_id=1,full_name='Сохранённый участник',status='activist'))
            await conn.execute(legacy.tables['quests'].insert().values(id=1,title='Сыграть в килу',emoji='⚽',thresholds='1,3,10'))
            await conn.execute(legacy.tables['member_quest_progress'].insert().values(member_id=1,quest_id=1,count=1))
            await conn.execute(legacy.tables['news_posts'].insert().values(author_user_id=1,text='Прежняя публикация'))
        # Remove exactly the schema that did not exist in the deployed release.
        async with old.begin() as conn:
            await conn.execute(text('DROP INDEX ix_news_posts_publication_state'))
            for name in ['publication_state','content_kind']+[c.name for c in NewsPost.__table__.columns if c.name.startswith('article_')]:
                await conn.execute(text('ALTER TABLE news_posts DROP COLUMN '+name))
            await conn.execute(text('ALTER TABLE member_quest_progress DROP COLUMN personal_seen_token'))
        import database.db
        monkeypatch.setattr(database.db,'engine',old)
        for name in sequence:
            module=importlib.import_module('scripts.'+name)
            monkeypatch.setattr(module,'engine',old)
            if hasattr(module,'async_session'):
                monkeypatch.setattr(module,'async_session',factory)
            if name == 'migrate_funding':
                # Old article migrations added VARCHAR fields before field
                # encryption existed. A funding migration must not silently
                # authenticate that legacy inventory or bless existing data.
                with pytest.raises(DataEncryptionError):
                    await module.migrate()
                encrypted_url = f"sqlite+aiosqlite:///{(tmp_path/'encrypted.db').as_posix()}"
                await migrate_database(str(old.url), encrypted_url)
                await old.dispose()
                old = create_async_engine(encrypted_url)
                factory = async_sessionmaker(old,expire_on_commit=False)
                monkeypatch.setattr(database.db,'engine',old)
                monkeypatch.setattr(module,'engine',old)
            await module.migrate()
        async with factory() as db:
            progress=(await db.execute(select(MemberQuestProgress))).scalar_one()
            post=(await db.execute(select(NewsPost))).scalar_one()
            assert progress.count==1 and progress.stars_claimed==0
            assert progress.personal_seen_token is None
            assert post.text=='Прежняя публикация' and post.publication_state=='published'
    finally:
        await old.dispose()
