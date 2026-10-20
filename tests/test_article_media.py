"""Media lifecycle and hostile inputs, using only isolated SQLite/storage."""
import asyncio
import io
import threading
import struct
import uuid

import pytest
from PIL import Image
from sqlalchemy import select

from config import STORAGE_DIR
from database.models import NewsPhoto, NewsPost
from services import images
from tests.conftest import login
from tests.test_articles import draft, save, publish


def picture(kind="PNG", *, animated=False, large=False):
    frame = Image.effect_noise((1800, 1200) if large else (80, 60), 90).convert("RGB")
    if kind == "PNG":
        frame = frame.convert("RGBA")
        frame.putpixel((0, 0), (0, 0, 0, 0))
    output = io.BytesIO()
    if animated:
        frames = [Image.new("RGB", (80, 60), (i * 3, 40, 90)) for i in range(80)]
        frames[0].save(output, kind, save_all=True, append_images=frames[1:], duration=40, loop=0)
    else:
        frame.save(output, kind)
    return output.getvalue()


async def upload(client, a, data, mime="image/png", **kwargs):
    return await client.post(f"/api/news/articles/{a['id']}/photos", data={"revision": a["revision"]},
                             files={"file": ("../photo.png", data, mime)}, **kwargs)


@pytest.mark.parametrize("kind,mime,animated", [
    ("JPEG", "image/jpeg", False), ("PNG", "image/png", False),
    ("GIF", "image/gif", True), ("WEBP", "image/webp", True), ("PNG", "image/png", True),
])
async def test_formats_survive_complete_article_lifecycle(client, session, world, kind, mime, animated):
    a = await save(client, await draft(client, world["federal"]))
    raw = picture(kind, animated=animated)
    response = await upload(client, a, raw, mime)
    assert response.status_code == 200, response.text
    a = response.json()
    # Upload has a durable document reference without a follow-up PUT.
    reopened = (await client.get(f"/api/news/articles/{a['id']}")).json()
    assert reopened["content"] == a["content"]
    photo = (await session.execute(select(NewsPhoto))).scalars().one()
    assert photo.stored_path.startswith(f"news/{a['id']}/")
    assert ".." not in photo.stored_path
    stored = Image.open(io.BytesIO((STORAGE_DIR / photo.stored_path).read_bytes()))
    if animated:
        assert stored.n_frames == Image.open(io.BytesIO(raw)).n_frames
        assert stored.n_frames > 1
        assert stored.info["loop"] == 0
    elif kind == "PNG":
        assert stored.convert("RGBA").getpixel((0, 0))[3] == 0
    published = await publish(client, a)
    assert published["photos"][0]["id"] == photo.id
    data = await client.get(published["photos"][0]["url"])
    assert data.status_code == 200 and data.headers["content-type"].startswith("image/")
    login(world["leader_tula"])
    assert (await client.get(f"/api/news/articles/{published['id']}")).status_code == 200


async def test_large_png_above_old_proxy_limit_is_valid_in_api(client, world):
    a = await save(client, await draft(client, world["federal"]))
    raw = picture(large=True)
    assert len(raw) > 512 * 1024
    response = await upload(client, a, raw)
    assert response.status_code == 200, response.text
    assert response.json()["media_limits"]["max_bytes"] >= len(raw)


@pytest.mark.parametrize("raw,mime", [
    (b"<svg xmlns='http://www.w3.org/2000/svg' onload='alert(1)'/>", "image/png"),
    (b"<html><script>alert(1)</script></html>", "image/gif"),
    (b"GIF89a\x00\x00", "image/gif"), (b"", "image/png"),
    (b"not a picture", "application/octet-stream"),
])
async def test_invalid_upload_preserves_revision_document_and_files(client, session, world, raw, mime):
    a = await save(client, await draft(client, world["federal"]))
    response = await upload(client, a, raw, mime)
    assert response.status_code == 400
    current = (await client.get(f"/api/news/articles/{a['id']}")).json()
    assert current["revision"] == a["revision"] and current["content"] == a["content"]
    assert not (await session.execute(select(NewsPhoto))).scalars().all()


async def test_actual_bytes_determine_safe_output_type(client, session, world):
    a = await draft(client, world["federal"])
    result = await upload(client, a, picture("JPEG"), "image/png")
    assert result.status_code == 200
    photo = (await session.execute(select(NewsPhoto))).scalars().one()
    assert photo.content_type == "image/jpeg" and photo.stored_path.endswith(".jpg")


async def test_upload_file_size_limit_is_atomic(client, world, monkeypatch):
    a = await save(client, await draft(client, world["federal"]))
    monkeypatch.setattr("services.news.MAX_UPLOAD_BYTES", 16)
    assert (await upload(client, a, picture())).status_code == 400
    current = (await client.get(f"/api/news/articles/{a['id']}")).json()
    assert current["revision"] == a["revision"] and current["photos"] == []


async def test_upload_stale_revision_and_foreign_author_cannot_write(client, world):
    old = await draft(client, world["federal"])
    a = await save(client, old)
    assert (await upload(client, old, picture())).status_code == 409
    login(world["superuser"])
    assert (await upload(client, a, picture())).status_code == 403
    login(world["federal"])
    current = (await client.get(f"/api/news/articles/{a['id']}")).json()
    assert current["photos"] == [] and current["revision"] == a["revision"]


async def test_upload_retry_with_identical_key_adds_one_image(client, world):
    a = await draft(client, world["federal"])
    headers = {"Idempotency-Key": str(uuid.uuid4())}
    raw = picture()
    first, second = await asyncio.gather(upload(client, a, raw, headers=headers), upload(client, a, raw, headers=headers))
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(first.json()["photos"]) == 1
    assert first.json()["revision"] == a["revision"] + 1


async def test_different_concurrent_requests_cannot_bypass_revision(client, world):
    a = await draft(client, world["federal"])
    results = await asyncio.gather(upload(client, a, picture()), upload(client, a, picture()))
    assert sorted(r.status_code for r in results) == [200, 409]
    current = (await client.get(f"/api/news/articles/{a['id']}")).json()
    assert len(current["photos"]) == 1


async def test_failed_document_update_removes_new_file(client, session, world):
    a = await save(client, await draft(client, world["federal"]), content=["x"] * 2500)
    before = set((STORAGE_DIR / "news").rglob("*")) if (STORAGE_DIR / "news").exists() else set()
    response = await upload(client, a, picture())
    assert response.status_code == 400
    assert not (await session.execute(select(NewsPhoto))).scalars().all()
    after = {p for p in (STORAGE_DIR / "news").rglob("*") if p.is_file()}
    assert not after - before
    current = (await client.get(f"/api/news/articles/{a['id']}")).json()
    assert current["revision"] == a["revision"]


async def test_photo_limit_and_delete_release_space(client, world):
    a = await save(client, await draft(client, world["federal"]))
    raw = picture()
    for _ in range(10):
        response = await upload(client, a, raw)
        assert response.status_code == 200, response.text
        a = response.json()
    assert (await upload(client, a, raw)).status_code == 400
    removed = a["photos"][0]
    content = [a["content"][0]]
    a = await save(client, a, content=content)
    assert a["photos"] == []
    assert (await client.get(removed["url"])).status_code == 404
    assert (await upload(client, a, raw)).status_code == 200


async def test_bad_second_news_photo_rolls_back_whole_post_and_files(client, session, world):
    login(world["leader_moscow"])
    before = set(STORAGE_DIR.rglob("*"))
    response = await client.post("/api/news", data={"text": "Не публиковать неполный альбом"}, files=[
        ("files", ("ok.png", picture(), "image/png")), ("files", ("bad.png", b"broken", "image/png")),
    ])
    assert response.status_code == 400
    assert not (await session.execute(select(NewsPost))).scalars().all()
    assert not (await session.execute(select(NewsPhoto))).scalars().all()
    assert not {p for p in STORAGE_DIR.rglob("*") if p.is_file()} - before


def test_broken_later_gif_frame_is_rejected():
    raw = picture("GIF", animated=True)
    # Keep the final image descriptor and data length but truncate its LZW data.
    corrupted = raw[:-12] + b"\x3b"
    with pytest.raises(images.InvalidImageError):
        images.shrink(corrupted, images.PHOTO_MAX_SIDE)


def test_broken_later_apng_frame_is_rejected():
    import zlib
    raw = bytearray(picture("PNG", animated=True))
    offset = 8
    while offset < len(raw):
        length = int.from_bytes(raw[offset:offset + 4], "big")
        if raw[offset + 4:offset + 8] == b"fdAT":
            # Keep chunk framing/CRC valid; break only the later frame's zlib.
            raw[offset + 12] ^= 255
            crc = zlib.crc32(raw[offset + 4:offset + 8 + length])
            raw[offset + 8 + length:offset + 12 + length] = crc.to_bytes(4, "big")
            break
        offset += 12 + length
    else:
        raise AssertionError("APNG frame missing")
    with pytest.raises(images.InvalidImageError):
        images.shrink(bytes(raw), images.PHOTO_MAX_SIDE)


async def test_storage_quota_rejects_upload_without_advancing_draft(client, world, monkeypatch):
    a = await save(client, await draft(client, world["federal"]))
    def no_space(size):
        raise ValueError("Недостаточно места в хранилище")
    monkeypatch.setattr("services.news.ensure_storage_capacity", no_space)
    response = await upload(client, a, picture())
    assert response.status_code == 400
    current = (await client.get(f"/api/news/articles/{a['id']}")).json()
    assert current["revision"] == a["revision"] and current["photos"] == []


def test_animation_budget_rejects_too_many_frames(monkeypatch):
    monkeypatch.setattr(images, "MAX_ANIMATION_FRAMES", 2)
    with pytest.raises(images.InvalidImageError, match="анимация"):
        images.shrink(picture("GIF", animated=True), images.PHOTO_MAX_SIDE)


def test_gif_limit_does_not_scan_unbounded_frame_count(monkeypatch):
    from PIL import GifImagePlugin
    raw = picture("GIF", animated=True)
    def forbidden_count(self):
        raise AssertionError("n_frames scans the entire GIF before applying the frame limit")
    monkeypatch.setattr(GifImagePlugin.GifImageFile, "n_frames", property(forbidden_count))
    monkeypatch.setattr(images, "MAX_ANIMATION_FRAMES", 3)
    with pytest.raises(images.InvalidImageError, match="анимация"):
        images.shrink(raw, images.PHOTO_MAX_SIDE)


def test_oversized_later_gif_frame_rejected_before_disposal_allocation(monkeypatch):
    raw = bytearray(picture("GIF", animated=True))
    position = 13 + (3 * (2 << (raw[10] & 7)) if raw[10] & 128 else 0)
    descriptors = []
    while raw[position] != 0x3B:
        marker = raw[position]
        if marker == 0x21:
            if raw[position + 1] == 0xF9:
                raw[position + 3] = (raw[position + 3] & ~28) | 8  # disposal=2
            position += 2
        elif marker == 0x2C:
            descriptors.append(position)
            packed = raw[position + 9]
            position += 10 + (3 * (2 << (packed & 7)) if packed & 128 else 0) + 1
        else:
            raise AssertionError("Unexpected GIF block")
        while raw[position]:
            position += raw[position] + 1
        position += 1
    # Use a size below Pillow's old default warning threshold, above our25MP.
    raw[descriptors[1] + 5:descriptors[1] + 9] = struct.pack("<HH", 6000, 5000)
    original = Image.core.fill
    allocations = []
    def bounded_fill(mode, size, *args, **kwargs):
        allocations.append(size)
        assert size[0] * size[1] <= images.MAX_IMAGE_PIXELS
        return original(mode, size, *args, **kwargs)
    monkeypatch.setattr(Image.core, "fill", bounded_fill)
    with pytest.raises(images.InvalidImageError):
        images.shrink(bytes(raw), images.PHOTO_MAX_SIDE)
    assert all(w * h <= images.MAX_IMAGE_PIXELS for w, h in allocations)


async def test_decoder_stays_bounded_even_when_request_is_cancelled(monkeypatch):
    started, release = threading.Event(), threading.Event()
    semaphore = asyncio.Semaphore(1)
    monkeypatch.setattr(images, "_decoder", semaphore)
    def blocking(payload, max_side):
        started.set()
        assert release.wait(5)
        return payload, "image/png"
    monkeypatch.setattr(images, "shrink", blocking)
    first = asyncio.create_task(images.prepare_image(b"first", 1600))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        with pytest.raises(images.ImageProcessorBusy):
            await images.prepare_image(b"second", 1600)
        assert semaphore.locked()
    finally:
        release.set()
        await asyncio.sleep(0.05)
    assert not semaphore.locked()
    assert await images.prepare_image(b"third", 1600) == (b"third", "image/png")
