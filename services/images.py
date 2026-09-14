"""Уменьшение картинок перед сохранением.

Файл с телефона приходит таким, каким его снял аппарат: 3–4 МБ и 4000 точек
по длинной стороне. Показываем мы его шириной в 350 точек, а ехать он всё
равно должен целиком — на мобильной сети это секунды ожидания. Поэтому перед
записью на диск картинка ужимается до размера, который кабинету действительно
нужен.

Оригинал не храним: место на сервере не бесплатное, а исходник в кабинете
никогда не показывается — ни в ленте, ни в полноэкранном просмотре, где
картинка всё равно вписывается в экран телефона.

Файл считается изображением только после успешного разбора Pillow. Битые,
замаскированные и чрезмерно большие изображения отклоняются до записи.
"""

import io
import logging
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)

# Длинная сторона после уменьшения. 1600 — с запасом на экран телефона с
# двойной плотностью и на полноэкранный просмотр с увеличением.
PHOTO_MAX_SIDE = 1600
# Аватар рисуется кружком 36 точек в ленте и 72 в профиле; 512 хватает с
# избытком на любую плотность экрана.
AVATAR_MAX_SIDE = 512

# Ограничение относится к распакованному изображению. Маленький сжатый файл
# может занимать сотни мегабайт после декодирования (decompression bomb).
MAX_IMAGE_PIXELS = 25_000_000
MAX_ANIMATION_FRAMES = 60
MAX_ANIMATION_TOTAL_PIXELS = 50_000_000

JPEG_QUALITY = 82

# Форматы, умеющие прозрачность: в JPEG её не перенести, поэтому такие
# картинки пересохраняем в их же формате.
TRANSPARENT_CAPABLE = {"PNG", "GIF", "WEBP"}

# Форматы, у которых несколько кадров действительно означают анимацию.
# Проверять по одному числу кадров нельзя: iPhone снимает HDR в MPO — это
# обычный JPEG, к которому приклеен второй кадр (карта яркости). Кадров в нём
# два, анимации никакой, и по числу кадров такие снимки — а это как раз самые
# тяжёлые, по 3–4 МБ — молча оставались бы нетронутыми.
ANIMATED_FORMATS = {"GIF", "WEBP", "PNG", "APNG"}


class InvalidImageError(ValueError):
    pass


def shrink(payload: bytes, max_side: int) -> tuple[bytes, str]:
    """Возвращает уменьшенную картинку и её content-type.

    Если картинка и так меньше предела, а формат обычный, отдаём исходные
    байты — незачем пересохранять и терять качество на ровном месте.
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(io.BytesIO(payload))
        with image:
            if image.width <= 0 or image.height <= 0 or image.width * image.height > MAX_IMAGE_PIXELS:
                raise InvalidImageError("Слишком большое разрешение изображения")
            image.load()
            source_format = (image.format or "").upper()
            content_type = _content_type(source_format)
            if not content_type:
                raise InvalidImageError("Неподдерживаемый формат изображения")
            # Анимацию не трогаем вовсе: кадры при пересохранении потеряются.
            if source_format in ANIMATED_FORMATS and getattr(image, "n_frames", 1) > 1:
                frames = image.n_frames
                if frames > MAX_ANIMATION_FRAMES or frames * image.width * image.height > MAX_ANIMATION_TOTAL_PIXELS:
                    raise InvalidImageError("Слишком большая анимация")
                return payload, content_type

            # Телефон пишет поворот в EXIF, а не в самих точках. Если его не
            # применить до уменьшения, снимок ляжет на диск повёрнутым — при
            # пересохранении EXIF пропадает, и выправлять будет уже нечем.
            image = ImageOps.exif_transpose(image)

            # Формат бережём только ради настоящей прозрачности. PNG без неё —
            # обычная картинка, и в PNG она весит в разы больше, чем в JPEG:
            # снимок экрана на 0,5 МБ превращается в 60 КБ без видимой разницы.
            keep_format = source_format in TRANSPARENT_CAPABLE and _has_alpha(image)
            fits = max(image.size) <= max_side

            if fits and keep_format:
                return payload, _content_type(source_format)

            if not fits:
                image.thumbnail((max_side, max_side), Image.LANCZOS)

            buffer = io.BytesIO()
            if keep_format:
                image.save(buffer, format=source_format)
                return buffer.getvalue(), _content_type(source_format)

            # Всё остальное (в том числе HEIC с телефона) — в JPEG: он есть
            # везде и для фотографии весит меньше всех.
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
            result = buffer.getvalue()

            # Плоская графика (логотип, снимок экрана, схема) в PNG жмётся
            # лучше, чем в JPEG, и перегон её только утяжелил бы. Раз размер
            # и так в пределах — оставляем как пришло.
            if fits and len(result) >= len(payload):
                return payload, _content_type(source_format)
            return result, "image/jpeg"

    except InvalidImageError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning, UnidentifiedImageError, OSError, ValueError) as exc:
        logger.warning("Отклонено некорректное изображение: %s", exc)
        raise InvalidImageError("Файл не удалось распознать как безопасное изображение") from exc


def _has_alpha(image) -> bool:
    """Есть ли в картинке хоть одна прозрачная точка. Режим с альфа-каналом
    сам по себе ещё ничего не значит: телефоны и редакторы сплошь и рядом
    отдают полностью непрозрачный RGBA."""
    if image.mode in ("RGBA", "LA"):
        alpha = image.getchannel("A")
        return alpha.getextrema()[0] < 255
    if image.mode == "P":
        return "transparency" in image.info
    return False


def _content_type(image_format: str) -> str:
    return {
        "JPEG": "image/jpeg",
        # MPO — контейнер вокруг обычного JPEG (HDR-снимок с iPhone).
        "MPO": "image/jpeg",
        "PNG": "image/png",
        "GIF": "image/gif",
        "WEBP": "image/webp",
    }.get(image_format, "")


def suffix_for(content_type: str) -> str:
    """Расширение, соответствующее типу после уменьшения: имя файла на диске
    не должно обещать HEIC там, где лежит уже JPEG."""
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
    }.get(content_type, "")
