"""Редактирование/архивирование региона и пользователя (services/admin_actions.py)
и физическое удаление явно пустых записей (scripts/admin.py region-purge/user-purge).

Пользователь просил не «удалить», а обратимо — is_active. Проверяем, что
архив реально прячет регион из access-фильтров (не просто флаг без эффекта),
а purge физически стирает только то, что действительно пусто, и честно
отказывает во всём остальном."""

import pytest

from database.models import Region, ROLE_COORDINATOR, User
from scripts.admin import cmd_region_purge, cmd_user_purge
from services.admin_actions import (
    archive_region,
    deactivate_user,
    reactivate_user,
    unarchive_region,
    update_region,
    update_user,
)
from utils.access import accessible_region_ids


async def test_update_region_renames_and_sets_genitive(session, world):
    region = await update_region(session, world["tula"].id, name="Тула Переименованная", genitive="Тулы")
    assert region.name == "Тула Переименованная"
    assert region.genitive_name == "Тулы"


async def test_update_region_rejects_name_collision(session, world):
    with pytest.raises(ValueError):
        await update_region(session, world["tula"].id, name="Москва")


async def test_archive_region_hides_it_from_access(session, world):
    leader = world["leader_moscow"]
    assert world["moscow"].id in await accessible_region_ids(session, leader)

    await archive_region(session, world["moscow"].id)
    assert world["moscow"].id not in await accessible_region_ids(session, leader)

    await unarchive_region(session, world["moscow"].id)
    assert world["moscow"].id in await accessible_region_ids(session, leader)


async def test_update_user_changes_name_and_clears_phone(session, world):
    user = await update_user(session, world["leader_tula"].id, full_name="Новое Имя", phone="+79990001122")
    assert user.full_name == "Новое Имя"
    assert user.phone == "+79990001122"

    cleared = await update_user(session, world["leader_tula"].id, phone="")
    assert cleared.phone is None


async def test_deactivate_and_reactivate_user(session, world):
    user_id = world["leader_tula"].id
    deactivated = await deactivate_user(session, user_id)
    assert deactivated.is_active is False

    reactivated = await reactivate_user(session, user_id)
    assert reactivated.is_active is True


async def test_region_purge_refuses_active_region(session, world, capsys):
    await cmd_region_purge(world["tula"].id, confirm=True)
    capsys.readouterr()
    region = await session.get(Region, world["tula"].id)
    assert region is not None  # ничего не удалено — регион не был в архиве


async def test_region_purge_deletes_empty_archived_region(session, world):
    region = Region(name="Пустой регион на удаление", is_active=False)
    session.add(region)
    await session.commit()
    await session.refresh(region)
    region_id = region.id

    await cmd_region_purge(region_id, confirm=False)
    assert await session.get(Region, region_id) is not None  # без --confirm ничего не удаляем

    # cmd_region_purge открывает свою собственную сессию (как в реальном CLI-
    # процессе) — identity-map сессии теста об этом не узнает сама, иначе
    # session.get() вернёт закешированный (ещё «живой») объект.
    await cmd_region_purge(region_id, confirm=True)
    session.expire_all()
    assert await session.get(Region, region_id) is None


async def test_user_purge_refuses_active_user(session, world):
    await cmd_user_purge(world["leader_tula"].id, confirm=True)
    assert await session.get(User, world["leader_tula"].id) is not None


async def test_user_purge_refuses_user_with_dependencies(session, world):
    """Руководитель Тулы деактивирован, но за ним числится регион — не пустая
    тестовая запись, физическое удаление обязано отказать."""
    await deactivate_user(session, world["leader_tula"].id)
    await cmd_user_purge(world["leader_tula"].id, confirm=True)
    assert await session.get(User, world["leader_tula"].id) is not None


async def test_user_purge_deletes_empty_inactive_user(session, world):
    user = User(full_name="Пустой Пользователь", role=ROLE_COORDINATOR, is_active=False)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    user_id = user.id

    await cmd_user_purge(user_id, confirm=False)
    assert await session.get(User, user_id) is not None  # без --confirm ничего не удаляем

    await cmd_user_purge(user_id, confirm=True)
    session.expire_all()
    assert await session.get(User, user_id) is None
