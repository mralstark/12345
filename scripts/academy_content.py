"""Owner-only publication of approved Academy content. No web editorial roles.

python -m scripts.academy_content --manifest /private/materials/catalog.json
Add --apply after checking the summary. Reusing keys updates entries in place;
omitted entries remain unchanged, is_active=false archives without deleting.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.routers.documents import _safe_original_name, _validated_document
from config import MAX_UPLOAD_BYTES, STORAGE_DIR, validate_security_config
from database.db import async_session, check_database_encryption, engine, init_db
from database.models import AcademyBook, AcademyTerm
from utils.private_files import write_private_file
from utils.storage import ensure_storage_capacity


class Entry(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    position: int = Field(default=0, ge=0, le=100000)
    is_active: bool = True


class BookIn(Entry):
    title: str = Field(min_length=1, max_length=255)
    file: str | None = Field(default=None, min_length=1, max_length=500)


class TermIn(Entry):
    term: str = Field(min_length=1, max_length=120)
    definition: str = Field(min_length=1, max_length=10000)


class Catalog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    books: list[BookIn] = Field(default_factory=list, max_length=500)
    terms: list[TermIn] = Field(default_factory=list, max_length=1000)

    @field_validator("books", "terms")
    @classmethod
    def unique_keys(cls, items):
        if len({item.key for item in items}) != len(items):
            raise ValueError("Repeated catalog key")
        return items


def pdf_payload(path: Path) -> bytes:
    if path.stat().st_size > MAX_UPLOAD_BYTES:
        raise ValueError("PDF exceeds MAX_UPLOAD_MB; split or optimize it before publication")
    with path.open("rb") as stream:
        payload = stream.read(MAX_UPLOAD_BYTES + 1)
    if len(payload) > MAX_UPLOAD_BYTES:
        raise ValueError("PDF exceeds MAX_UPLOAD_MB")
    if path.suffix.lower() != ".pdf":
        raise ValueError("Only PDF books are supported")
    _validated_document(payload, path.name)
    return payload


async def import_catalog(manifest: Path, session: AsyncSession, *, apply: bool = False) -> dict:
    manifest = manifest.resolve()
    if manifest.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("Catalog exceeds 2 MiB")
    catalog = Catalog.model_validate_json(manifest.read_bytes())
    books = {b.key: b for b in (await session.execute(select(AcademyBook))).scalars()}
    terms = {t.key: t for t in (await session.execute(select(AcademyTerm))).scalars()}
    if len(set(books) | {b.key for b in catalog.books}) > 500 or len(set(terms) | {t.key for t in catalog.terms}) > 1000:
        raise ValueError("Catalog capacity exceeded")
    files = {}
    # Validate the complete input before modifying any rows or writing private files.
    for item in catalog.books:
        if item.file is None:
            if item.key not in books:
                raise ValueError("A new book requires its approved PDF")
            continue
        source = manifest.parent / item.file
        if source.is_symlink() or not source.resolve().is_relative_to(manifest.parent):
            raise ValueError("PDF must be inside the manifest directory")
        payload = await asyncio.to_thread(pdf_payload, source)
        files[item.key] = (source, hashlib.sha256(payload).hexdigest(), len(payload))
    result = {"books": len(catalog.books), "terms": len(catalog.terms), "applied": apply}
    if not apply:
        return result
    created = []
    try:
        for item in catalog.books:
            book = books.get(item.key)
            file = files.get(item.key)
            if file and (book is None or file[1] != book.source_sha256):
                source, digest, size = file
                payload = await asyncio.to_thread(pdf_payload, source)
                if hashlib.sha256(payload).hexdigest() != digest:
                    raise ValueError("PDF changed during publication; retry")
                ensure_storage_capacity(size + 128)
                relative = "academy/books/" + uuid.uuid4().hex + ".pdf"
                target = STORAGE_DIR / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                await asyncio.to_thread(write_private_file, target, payload)
                created.append(target)
                if book is None:
                    book = AcademyBook(key=item.key)
                    session.add(book)
                book.stored_path, book.source_sha256, book.size_bytes = relative, digest, size
                book.original_name = _safe_original_name(source.name)
            book.title, book.position, book.is_active = item.title, item.position, item.is_active
        for item in catalog.terms:
            term = terms.get(item.key)
            if term is None:
                term = AcademyTerm(key=item.key)
                session.add(term)
            term.term, term.definition = item.term, item.definition
            term.position, term.is_active = item.position, item.is_active
        await session.commit()
    except BaseException:
        await session.rollback()
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return result


async def main(args):
    validate_security_config()
    await init_db()
    await check_database_encryption()
    try:
        async with async_session() as session:
            print(json.dumps(await import_catalog(Path(args.manifest), session, apply=args.apply)))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--apply", action="store_true")
    try:
        asyncio.run(main(parser.parse_args()))
    except Exception as error:
        # Validation exceptions can contain submitted text or private filenames.
        print(json.dumps({"ok": False, "error_type": type(error).__name__}))
        raise SystemExit(1)
