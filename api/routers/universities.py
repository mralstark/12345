"""Каталог вузов — общий на всю систему, без региональной привязки
(см. utils/university_cells.py, database/models.py::University)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user, get_db
from database.models import University, User

router = APIRouter(prefix="/universities", tags=["universities"])


def _university_dict(university: University) -> dict:
    return {"id": university.id, "name": university.name, "region_id": university.region_id}


@router.get("")
async def search_universities(
    q: str = "",
    region_id: int | None = None,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Автодополнение в форме «Состав»/«Личная информация». Без region_id —
    справочник целиком (существующее поведение, регион там уже известен из
    контекста человека и путаница маловероятна). С region_id — только вузы
    этого региона: так пользуется форма регистрации (api/routers/register.py),
    где до выбора отделения вообще ничего не ограничено."""
    stmt = select(University).order_by(University.name)
    if region_id is not None:
        stmt = stmt.where(University.region_id == region_id)
    result = await session.execute(stmt)
    items = list(result.scalars().all())

    # Регистронезависимый поиск считаем в Python: lower() в SQLite не
    # приводит кириллицу к нижнему регистру (тот же нюанс, что и в
    # api/routers/members.py::list_members).
    needle = q.strip().lower()
    if needle:
        items = [u for u in items if needle in u.name.lower()]

    return {"items": [_university_dict(u) for u in items[:20]]}


class UniversityIn(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    region_id: int | None = None


@router.post("")
async def create_university(
    payload: UniversityIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Создаёт вуз или отдаёт уже существующий с таким именем без учёта
    регистра — руководитель может смело вписывать вуз, которого ещё нет,
    не рискуя задвоить каталог опечаткой в регистре. region_id проставляется
    только при создании новой записи — у уже существующей не переписывается
    (одна и та же ячейка каталога общая на всю систему, а не своя на регион)."""
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "Название вуза не может быть пустым")

    key = name.lower()
    existing = (await session.execute(select(University))).scalars().all()
    for university in existing:
        if university.name.lower() == key:
            return _university_dict(university)

    university = University(name=name, region_id=payload.region_id)
    session.add(university)
    await session.commit()
    await session.refresh(university)
    return _university_dict(university)
