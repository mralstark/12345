"""Подпись роли для показа в кабинете и в боте.

Руководителю показываем не общую формулировку («Руководитель регионального
отделения»), а с названием его отделения — «Руководитель — Академисты |
Красноярск» (единый формат «Академисты | Регион», без родительного падежа —
он был источником путаницы и ошибок при заведении новых регионов, см.
region_display_name ниже), у руководителя вузовской ячейки — с названием вуза
(«Руководитель ячейки СПбГУ»). Падежа нет и здесь: «Руководитель Академистов
{вуз}» требовал родительного, из-за чего у ячейки было отдельное поле
genitive_name, которое надо было склонять руками при каждом заведении. После
слова «ячейки» название стоит в именительном и склонять нечего.
Остальным ролям (координатор, федеральный, superuser) — общая подпись из
ROLE_LABELS, они не привязаны к одному конкретному региону/ячейке.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    ROLE_CELL_LEADER,
    ROLE_LABELS,
    ROLE_LEADER,
    ROLE_PARTICIPANT,
    Region,
    UniversityCell,
    User,
)


def region_display_name(region: Region) -> str:
    """Единый формат подписи региона — «Академисты | Красноярск» — без
    родительного падежа: раньше он брался из Region.genitive_name, которое
    нужно было каждый раз правильно склонять вручную при создании региона —
    источник постоянных ошибок и путаницы. Теперь один и тот же вид везде,
    где регион подписывается брендом «Академисты» (см. api/routers/register.py)."""
    return f"Академисты | {region.name}"


async def role_label(session: AsyncSession, user: User) -> str | None:
    """None — показывать нечего.

    У участника роль есть только внутри системы: она означает «управленческих
    прав нет», и человеку это ничего не говорит. Хуже того, ярлык «Участник
    Братства» спорил со статусом: у активиста в Братство ещё не посвящённого
    выходило, будто он уже участник. Кто человек в Братстве, говорит статус
    (MEMBER_STATUS_LABELS), и второй подписи рядом с ним не нужно.
    """
    if user.role == ROLE_PARTICIPANT:
        return None
    if user.role == ROLE_LEADER:
        region = (
            await session.execute(select(Region).where(Region.leader_user_id == user.id))
        ).scalar_one_or_none()
        if region is not None:
            return f"Руководитель — {region_display_name(region)}"
    if user.role == ROLE_CELL_LEADER:
        cell = (
            await session.execute(select(UniversityCell).where(UniversityCell.leader_user_id == user.id))
        ).scalar_one_or_none()
        if cell is not None:
            return f"Руководитель ячейки {cell.name}"
    return ROLE_LABELS.get(user.role, user.role)
