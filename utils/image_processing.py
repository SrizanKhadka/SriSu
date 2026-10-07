"""Fail-closed normalization for legacy chat photo uploads."""

from io import BytesIO
import os
import warnings

from django.core.files.base import ContentFile
from django.utils.text import get_valid_filename
from PIL import Image, ImageOps, UnidentifiedImageError


ALLOWED_FORMATS = {"JPEG", "PNG"}
MAX_DIMENSION = 8192
MAX_PIXELS = 25_000_000
MAX_OUTPUT_DIMENSION = 2000
MAX_OUTPUT_BYTES = 5 * 1024 * 1024


class InvalidImage(ValueError):
    pass


def _open_verified(uploaded_image):
    try:
        uploaded_image.seek(0)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            probe = Image.open(uploaded_image)
            detected_format = probe.format
            width, height = probe.size
            if detected_format not in ALLOWED_FORMATS:
                raise InvalidImage("Only JPEG and PNG photos are supported.")
            if (
                width <= 0
                or height <= 0
                or width > MAX_DIMENSION
                or height > MAX_DIMENSION
                or width * height > MAX_PIXELS
            ):
                raise InvalidImage("Image dimensions are too large.")
            if getattr(probe, "is_animated", False):
                raise InvalidImage("Animated images are not supported.")
            probe.verify()

        uploaded_image.seek(0)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            decoded = Image.open(uploaded_image)
            decoded.load()
            return ImageOps.exif_transpose(decoded).copy()
    except InvalidImage:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise InvalidImage("Image dimensions are too large.") from None
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        raise InvalidImage("The uploaded file is not a valid JPEG or PNG image.") from None
    finally:
        try:
            uploaded_image.seek(0)
        except (AttributeError, OSError):
            pass


def _flatten_to_rgb(image: Image.Image) -> Image.Image:
    if image.mode in ("RGBA", "LA") or "transparency" in image.info:
        foreground = image.convert("RGBA")
        background = Image.new("RGB", foreground.size, "white")
        background.paste(foreground, mask=foreground.getchannel("A"))
        return background
    return image.convert("RGB")


def process_image(uploaded_image):
    """Verify, fully decode, orient, resize and re-encode without source metadata."""
    image = _flatten_to_rgb(_open_verified(uploaded_image))
    image.thumbnail(
        (MAX_OUTPUT_DIMENSION, MAX_OUTPUT_DIMENSION),
        Image.Resampling.LANCZOS,
    )

    encoded = None
    for quality in range(90, 34, -5):
        buffer = BytesIO()
        image.save(
            buffer,
            format="JPEG",
            quality=quality,
            optimize=True,
            progressive=True,
        )
        if buffer.tell() <= MAX_OUTPUT_BYTES:
            encoded = buffer.getvalue()
            break
    if encoded is None:
        raise InvalidImage("The normalized image is too large.")

    original_name = os.path.basename(getattr(uploaded_image, "name", "image"))
    stem = get_valid_filename(os.path.splitext(original_name)[0]) or "image"
    return ContentFile(encoded, name=f"{stem}.jpg")
