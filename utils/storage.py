"""Общие ограничения дискового хранилища."""

import shutil
from pathlib import Path

from config import MAX_STORAGE_BYTES, MIN_FREE_STORAGE_BYTES, STORAGE_DIR


def _existing_parent(path: Path) -> Path:
    candidate = path.resolve()
    while not candidate.exists() and candidate.parent != candidate:
        candidate = candidate.parent
    return candidate


def storage_size_bytes(root: Path = STORAGE_DIR) -> int:
    if not root.exists():
        return 0
    total = 0
    for entry in root.rglob("*"):
        if entry.is_file() and not entry.is_symlink():
            try:
                total += entry.stat().st_size
            except OSError:
                continue
    return total


def ensure_storage_capacity(incoming_bytes: int) -> None:
    """Проверяет общую квоту и сохраняет резерв места для БД и ОС."""
    if incoming_bytes < 0:
        raise ValueError("Некорректный размер файла")
    if storage_size_bytes() + incoming_bytes > MAX_STORAGE_BYTES:
        raise ValueError("Хранилище заполнено: обратитесь к администратору")
    free = shutil.disk_usage(_existing_parent(STORAGE_DIR)).free
    if free - incoming_bytes < MIN_FREE_STORAGE_BYTES:
        raise ValueError("На сервере недостаточно свободного места для загрузки")
