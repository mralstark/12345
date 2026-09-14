"""Уменьшение картинок перед сохранением.

Смысл проверок: файл с телефона не должен ехать к читателю целиком ради
картинки шириной в 350 точек, но и терять при этом ничего не должен —
ни прозрачность, ни поворот, ни саму возможность сохраниться.
"""

import io

import pytest
from PIL import Image

from services.images import AVATAR_MAX_SIDE, PHOTO_MAX_SIDE, InvalidImageError, shrink, suffix_for
from services.news import PHOTO_LIMIT
from tests.conftest import login


def _jpeg(width, height, quality=95):
    buffer = io.BytesIO()
    Image.effect_mandelbrot((width, height), (-2, -1.5, 1, 1.5), 60).convert("RGB").save(
        buffer, "JPEG", quality=quality
    )
    return buffer.getvalue()


def _png(width, height, transparent=False):
    image = Image.new("RGBA", (width, height), (217, 181, 123, 255))
    if transparent:
        image.putpixel((0, 0), (0, 0, 0, 0))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def _open(payload):
    return Image.open(io.BytesIO(payload))


def test_phone_photo_is_scaled_down_to_screen_size():
    """Снимок современного телефона — 4000 точек по длинной стороне; кабинету
    столько не нужно ни в ленте, ни в полноэкранном просмотре."""
    raw = _jpeg(4032, 3024)
    out, content_type = shrink(raw, PHOTO_MAX_SIDE)

    assert max(_open(out).size) == PHOTO_MAX_SIDE
    assert content_type == "image/jpeg"
    assert len(out) < len(raw)


def test_small_photo_is_left_alone_in_size():
    """Картинка меньше предела не растягивается — уменьшение не должно
    превращаться в увеличение."""
    raw = _jpeg(800, 600)
    out, _ = shrink(raw, PHOTO_MAX_SIDE)
    assert _open(out).size == (800, 600)


def test_transparency_survives():
    """Прозрачность в JPEG не перенести, поэтому такую картинку оставляем в
    её формате."""
    raw = _png(800, 800, transparent=True)
    out, content_type = shrink(raw, PHOTO_MAX_SIDE)

    assert content_type == "image/png"
    assert _open(out).mode in ("RGBA", "LA", "P")


def test_opaque_png_becomes_jpeg():
    """Фотографический PNG без единой прозрачной точки — обычная картинка, и
    в PNG она весит в разы больше. Режим RGBA сам по себе поводом беречь
    формат не является: редакторы сплошь и рядом отдают непрозрачный RGBA."""
    buffer = io.BytesIO()
    Image.effect_mandelbrot((900, 900), (-2, -1.5, 1, 1.5), 60).convert("RGBA").save(buffer, "PNG")
    raw = buffer.getvalue()

    out, content_type = shrink(raw, PHOTO_MAX_SIDE)

    assert content_type == "image/jpeg"
    assert len(out) < len(raw)


def test_flat_graphic_is_not_made_heavier():
    """Плоская графика — логотип, схема, снимок экрана — в PNG жмётся лучше,
    чем в JPEG. Перегон её только утяжелил бы, поэтому оставляем как пришло."""
    raw = _png(900, 900, transparent=False)
    out, _ = shrink(raw, PHOTO_MAX_SIDE)
    assert len(out) <= len(raw)


def test_avatar_is_cut_to_its_own_size():
    """Аватар рисуется кружком в 36 точек — держать под него снимок в 2000
    незачем."""
    out, _ = shrink(_jpeg(2000, 2000), AVATAR_MAX_SIDE)
    assert max(_open(out).size) == AVATAR_MAX_SIDE


def test_exif_rotation_is_applied_before_saving():
    """Телефон пишет поворот в EXIF, а не в самих точках. При пересохранении
    EXIF пропадает — если поворот не применить заранее, снимок ляжет на диск
    лежащим на боку, и выправлять будет уже нечем."""
    image = Image.effect_mandelbrot((1200, 600), (-2, -1.5, 1, 1.5), 60).convert("RGB")
    exif = Image.Exif()
    exif[274] = 6  # «повернуть на 90°» — обычная ориентация вертикального снимка
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", exif=exif)

    out, _ = shrink(buffer.getvalue(), PHOTO_MAX_SIDE)
    # Стороны поменялись местами — значит поворот применён к точкам.
    assert _open(out).size == (600, 1200)


def test_iphone_hdr_photo_is_not_mistaken_for_animation():
    """iPhone снимает HDR в MPO: обычный JPEG, к которому приклеен второй кадр
    с картой яркости. Кадров два, анимации никакой. Проверять по одному числу
    кадров нельзя — самые тяжёлые снимки, по 3–4 МБ, молча оставались бы
    нетронутыми (так и случилось на боевом сервере)."""
    frame = Image.effect_mandelbrot((3000, 2000), (-2, -1.5, 1, 1.5), 60).convert("RGB")
    buffer = io.BytesIO()
    frame.save(buffer, "MPO", save_all=True, append_images=[frame.copy()])
    raw = buffer.getvalue()
    assert Image.open(io.BytesIO(raw)).format == "MPO"

    out, content_type = shrink(raw, PHOTO_MAX_SIDE)

    assert content_type == "image/jpeg"
    assert max(_open(out).size) == PHOTO_MAX_SIDE
    assert len(out) < len(raw)


def test_real_animation_is_left_alone():
    """А настоящую анимацию не трогаем: кадры при пересохранении потерялись бы."""
    frames = [Image.new("P", (60, 60), i) for i in range(3)]
    buffer = io.BytesIO()
    frames[0].save(buffer, "GIF", save_all=True, append_images=frames[1:])
    raw = buffer.getvalue()

    out, content_type = shrink(raw, PHOTO_MAX_SIDE)

    assert out == raw
    assert content_type == "image/gif"


def test_broken_file_is_rejected():
    broken = b"\x00\x01 not an image at all"
    with pytest.raises(InvalidImageError):
        shrink(broken, PHOTO_MAX_SIDE)


@pytest.mark.parametrize(
    "content_type,expected",
    [("image/jpeg", ".jpg"), ("image/png", ".png"), ("image/webp", ".webp"), ("", "")],
)
def test_suffix_matches_the_type_after_shrinking(content_type, expected):
    """Имя файла на диске не должно обещать HEIC там, где лежит уже JPEG."""
    assert suffix_for(content_type) == expected


# --- Сколько фотографий можно приложить ---------------------------------------


async def test_post_within_the_photo_limit_publishes(client, world):
    """Предел не должен мешать обычному посту с несколькими фотографиями."""
    login(world["leader_moscow"])
    files = [("files", (f"{i}.png", _png(40, 40), "image/png")) for i in range(PHOTO_LIMIT)]

    response = await client.post("/api/news", data={"text": "Съезд"}, files=files)

    assert response.status_code == 200
    assert response.json()["photos"] == PHOTO_LIMIT


async def test_too_many_photos_do_not_leave_a_post_behind(client, world):
    """Отказ приходит до записи новости. Иначе в ленте остался бы пост без
    картинок, которые автор к нему и прикладывал."""
    login(world["leader_moscow"])
    files = [("files", (f"{i}.png", _png(40, 40), "image/png")) for i in range(PHOTO_LIMIT + 1)]

    response = await client.post("/api/news", data={"text": "Съезд"}, files=files)

    assert response.status_code == 400
    assert str(PHOTO_LIMIT) in response.json()["detail"]
    assert (await client.get("/api/news")).json()["items"] == []
