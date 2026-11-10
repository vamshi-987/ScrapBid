"""Shared image cleaning: validate the real format, fix rotation, strip all metadata.

Used for listing photos, dealer document images and (later) scale photos.
"""

import io
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

# Decoded format -> (content type, file extension, save options)
ALLOWED_FORMATS = {
    "JPEG": ("image/jpeg", ".jpg", {"quality": 85, "optimize": True}),
    "PNG": ("image/png", ".png", {"optimize": True}),
    "WEBP": ("image/webp", ".webp", {"quality": 85}),
}

# Refuse images that would decode to more pixels than this (decompression bombs).
MAX_PIXELS = 40_000_000


class InvalidImage(Exception):
    """The upload isn't an allowed, readable image."""


class CleanImage:
    def __init__(self, data, content_type, extension, width, height):
        self.data = data
        self.content_type = content_type
        self.extension = extension
        self.width = width
        self.height = height


def clean_image(uploaded_file, max_dimension=None):
    """Decode, re-encode without any metadata (EXIF, GPS, text chunks) and optionally shrink.

    EXIF rotation is applied first so the picture still shows the right way up.
    """
    uploaded_file.seek(0)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(uploaded_file) as original:
                image_format = original.format
                if image_format not in ALLOWED_FORMATS:
                    raise InvalidImage("unsupported_type")
                if original.width * original.height > MAX_PIXELS:
                    raise InvalidImage("too_many_pixels")
                original.load()
                image = ImageOps.exif_transpose(original)
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError):
        raise InvalidImage("unsupported_type") from None
    except Image.DecompressionBombWarning:
        raise InvalidImage("too_many_pixels") from None

    if max_dimension:
        image.thumbnail((max_dimension, max_dimension))
    if image_format == "JPEG" and image.mode not in ("RGB", "L"):
        image = image.convert("RGB")

    # A fresh image holds only pixels: no EXIF, ICC, XMP or PNG text from the upload.
    pixels_only = Image.new(image.mode, image.size)
    pixels_only.paste(image)
    if image.mode == "P":
        pixels_only.putpalette(image.getpalette())

    content_type, extension, options = ALLOWED_FORMATS[image_format]
    buffer = io.BytesIO()
    pixels_only.save(buffer, format=image_format, **options)
    return CleanImage(
        buffer.getvalue(), content_type, extension, pixels_only.width, pixels_only.height
    )
