"""Ужимает фотографии и аватары, загруженные до появления уменьшения.

Новые файлы ужимаются при загрузке (services/images.py), но всё, что легло на
диск раньше, лежит там в оригинале — снимки по 3–4 МБ ради картинки шириной в
350 точек. Этот скрипт проходит по уже сохранённому и приводит к тому же виду.

    python -m scripts.shrink_existing_images            # показать, что будет
    python -m scripts.shrink_existing_images --apply    # сделать

Без --apply ничего не меняется: сначала смотрим отчёт, потом решаем.

Файл, который не стал меньше, не трогаем вовсе — переписывать его значило бы
потерять качество без всякой выгоды. Запись новая, старая удаляется только
после успешной записи новой: оборвись всё посередине, картинка останется на
месте, а не исчезнет.
"""

import argparse
import asyncio
from pathlib import Path

from sqlalchemy import select

from config import STORAGE_DIR
from database.db import async_session
from database.models import Member, NewsPhoto
from services.images import AVATAR_MAX_SIDE, PHOTO_MAX_SIDE, shrink, suffix_for


# Насколько файл должен похудеть, чтобы его стоило переписывать.
MIN_SAVING_SHARE = 0.10


def _mb(value: int) -> str:
    return f"{value / 1048576:.2f} МБ"


async def process(apply: bool) -> None:
    saved_total = 0
    touched = 0
    skipped = 0

    async with async_session() as session:
        photos = list((await session.execute(select(NewsPhoto))).scalars().all())
        members = list(
            (await session.execute(select(Member).where(Member.avatar_path.is_not(None)))).scalars().all()
        )

        for photo in photos:
            saved = await _shrink_one(
                session, STORAGE_DIR / photo.stored_path, PHOTO_MAX_SIDE, apply,
                label=f"фото #{photo.id}", owner=photo,
            )
            if saved is None:
                skipped += 1
            else:
                touched += 1
                saved_total += saved

        for member in members:
            saved = await _shrink_one(
                session, STORAGE_DIR / member.avatar_path, AVATAR_MAX_SIDE, apply,
                label=f"аватар {member.full_name}", owner=member,
            )
            if saved is None:
                skipped += 1
            else:
                touched += 1
                saved_total += saved

        if apply:
            await session.commit()

    print()
    print(f"Ужать можно: {touched}, оставлено как есть: {skipped}")
    print(f"Освободится: {_mb(saved_total)}")
    if not apply:
        print("Это была примерка. Повторите с --apply, чтобы применить.")


async def _shrink_one(session, path: Path, max_side: int, apply: bool, label: str, owner) -> int | None:
    """Возвращает сэкономленные байты или None, если файл трогать не нужно."""
    if not path.is_file():
        print(f"  {label}: файла нет на диске ({path}) — пропускаю")
        return None

    raw = path.read_bytes()
    payload, content_type = shrink(raw, max_side)
    saved = len(raw) - len(payload)
    # Порог, а не «стало хоть на байт меньше»: уже ужатый файл пересохраняется
    # чуть меньшим каждый раз, и без порога повторный запуск раз за разом
    # терял бы качество ради сотни байт. Ниже порога считаем, что делать
    # нечего, — и скрипт становится безопасным для повторов.
    if saved < len(raw) * MIN_SAVING_SHARE:
        return None

    print(f"  {label}: {_mb(len(raw))} -> {_mb(len(payload))}")
    if not apply:
        return saved

    # Расширение могло смениться (HEIC/PNG стали JPEG) — тогда и путь другой.
    suffix = suffix_for(content_type)
    target = path.with_suffix(suffix) if suffix else path
    target.write_bytes(payload)

    relative = target.relative_to(STORAGE_DIR).as_posix()
    if isinstance(owner, NewsPhoto):
        owner.stored_path = relative
        owner.size_bytes = len(payload)
        if content_type:
            owner.content_type = content_type
    else:
        owner.avatar_path = relative

    if target != path:
        path.unlink()
    return saved


def main() -> None:
    parser = argparse.ArgumentParser(description="Ужать уже загруженные картинки")
    parser.add_argument("--apply", action="store_true", help="применить, а не только показать")
    args = parser.parse_args()
    asyncio.run(process(args.apply))


if __name__ == "__main__":
    main()
