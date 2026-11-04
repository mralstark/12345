"""Shared library access: an active registered account, independent of region/status."""
import re
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from config import STORAGE_DIR
from database.models import AcademyBook

_BOOK_PATH = re.compile(r"academy/books/[a-f0-9]{32}\.pdf")


async def authorized_book(session: AsyncSession, book_id: int) -> tuple[AcademyBook, Path]:
    # Call only after the account dependency or native-ticket account revalidation.
    book = await session.get(AcademyBook, book_id)
    if book is None or not book.is_active:
        raise HTTPException(404, "Книга не найдена")
    if not _BOOK_PATH.fullmatch(book.stored_path):
        raise HTTPException(404, "Файл книги недоступен")
    root = STORAGE_DIR.resolve()
    path = STORAGE_DIR / book.stored_path
    if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
        raise HTTPException(404, "Файл книги недоступен")
    return book, path
