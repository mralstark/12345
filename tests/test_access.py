"""Правила видимости из ТЗ §3 — главное, что нельзя сломать незаметно."""

import pytest

from utils.access import (
    AccessDenied,
    accessible_region_ids,
    actor_cell,
    can_edit_region,
    can_message,
    can_view_region,
    correspondents,
    require_edit,
)


async def test_leader_sees_only_own_region(session, world):
    ids = await accessible_region_ids(session, world["leader_moscow"])
    assert ids == [world["moscow"].id]


async def test_coordinator_sees_assigned_regions_only(session, world):
    ids = await accessible_region_ids(session, world["coordinator"])
    assert ids == [world["moscow"].id]
    assert not await can_view_region(session, world["coordinator"], world["tula"].id)


async def test_federal_sees_all_regions(session, world):
    ids = await accessible_region_ids(session, world["federal"])
    assert set(ids) == {world["moscow"].id, world["tula"].id}


async def test_coordinator_cannot_edit_region_data(session, world):
    """Координатор смотрит регион, но не правит его данные (ТЗ §6, §7, §9)."""
    assert await can_view_region(session, world["coordinator"], world["moscow"].id)
    assert not await can_edit_region(session, world["coordinator"], world["moscow"].id)
    with pytest.raises(AccessDenied):
        await require_edit(session, world["coordinator"], world["moscow"].id)


async def test_leader_edits_own_region(session, world):
    assert await can_edit_region(session, world["leader_moscow"], world["moscow"].id)
    assert not await can_edit_region(session, world["leader_moscow"], world["tula"].id)


async def test_leader_writes_up_the_vertical(session, world):
    people = await correspondents(session, world["leader_moscow"])
    names = {person.id for person in people}
    assert world["coordinator"].id in names
    assert world["federal"].id in names
    # Руководителю другого региона писать некуда — горизонтальных связей нет.
    assert world["leader_tula"].id not in names


async def test_coordinator_writes_only_to_own_leaders(session, world):
    assert await can_message(session, world["coordinator"], world["leader_moscow"].id)
    assert not await can_message(session, world["coordinator"], world["leader_tula"].id)


async def test_cell_leader_sees_and_edits_only_parent_region(session, world):
    """Руководитель ячейки проходит региональную проверку (иначе require_edit(region_id)
    отказал бы целиком) — сужение до самой ячейки отдельным слоем в роутерах."""
    ids = await accessible_region_ids(session, world["cell_leader"])
    assert ids == [world["moscow"].id]
    assert await can_edit_region(session, world["cell_leader"], world["moscow"].id)
    assert not await can_edit_region(session, world["cell_leader"], world["tula"].id)

    cell = await actor_cell(session, world["cell_leader"])
    assert cell is not None and cell.id == world["mgimo"].id
    assert await actor_cell(session, world["leader_moscow"]) is None
