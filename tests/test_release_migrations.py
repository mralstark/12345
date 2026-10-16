"""Run the actual deployment sequences against a pre-articles database."""
import importlib
from pathlib import Path
import re

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import Base, Member, MemberQuestProgress, NewsPost, Quest, Region, User


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
        async with old.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as db:
            region=Region(name='Сохранённый регион');author=User(full_name='Сохранённый автор',role='federal')
            db.add_all([region,author]);await db.flush()
            member=Member(region_id=region.id,full_name='Сохранённый участник',status='activist')
            quest=Quest(title='Сыграть в килу',emoji='⚽',thresholds='1,3,10')
            db.add_all([member,quest]);await db.flush()
            db.add(MemberQuestProgress(member_id=member.id,quest_id=quest.id,count=1))
            db.add(NewsPost(author_user_id=author.id,text='Прежняя публикация'))
            await db.commit()
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
            await module.migrate()
        async with factory() as db:
            progress=(await db.execute(select(MemberQuestProgress))).scalar_one()
            post=(await db.execute(select(NewsPost))).scalar_one()
            assert progress.count==1 and progress.stars_claimed==0
            assert progress.personal_seen_token is None
            assert post.text=='Прежняя публикация' and post.publication_state=='published'
    finally:
        await old.dispose()
