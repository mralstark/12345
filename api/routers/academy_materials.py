"""Books and vocabulary for all active registered users; no public static files."""
from fastapi import APIRouter, Depends, Path as PathParam
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user, get_db
from database.models import AcademyBook, AcademyTerm, User
from services.academy_materials import authorized_book
from utils.private_files import private_file_response

router = APIRouter(prefix="/academy", tags=["academy"])


@router.get("/books")
async def books(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)) -> dict:
    rows = (await session.execute(select(AcademyBook).where(AcademyBook.is_active.is_(True))
                                  .order_by(AcademyBook.position, AcademyBook.id))).scalars()
    return {"items": [{"id": b.id, "title": b.title, "size_bytes": b.size_bytes} for b in rows]}


@router.get("/vocabulary")
async def vocabulary(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)) -> dict:
    rows = (await session.execute(select(AcademyTerm).where(AcademyTerm.is_active.is_(True))
                                  .order_by(AcademyTerm.position, AcademyTerm.id))).scalars()
    return {"items": [{"id": t.id, "term": t.term, "definition": t.definition} for t in rows]}


@router.get("/books/{book_id}/download")
async def download_book(book_id: int = PathParam(gt=0, le=2**63-1), user: User = Depends(get_current_user),
                        session: AsyncSession = Depends(get_db)) -> Response:
    book, path = await authorized_book(session, book_id)
    return await private_file_response(path, filename=book.original_name, media_type="application/pdf")
