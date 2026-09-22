"""Совмещаемые полномочия пользователя.

Основное поле ``User.role`` сохранено для обратной совместимости. Новые
флаги дополняют его и позволяют не затирать одну роль другой.
"""

from database.models import ROLE_COORDINATOR, ROLE_FEDERAL, ROLE_SUPERUSER, User


def has_role(user: User, role: str) -> bool:
    if user.role == role:
        return True
    if role == ROLE_SUPERUSER:
        return bool(user.is_superuser)
    if role == ROLE_FEDERAL:
        return bool(user.is_federal)
    if role == ROLE_COORDINATOR:
        return bool(user.is_coordinator)
    return False


def has_any_role(user: User, roles: tuple[str, ...] | list[str] | set[str]) -> bool:
    return any(has_role(user, role) for role in roles)


def role_codes(user: User) -> list[str]:
    result = [user.role]
    for role in (ROLE_COORDINATOR, ROLE_FEDERAL, ROLE_SUPERUSER):
        if has_role(user, role) and role not in result:
            result.append(role)
    return result
