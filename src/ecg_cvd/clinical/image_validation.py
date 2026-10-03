"""Validation for image-only clinical ECG source artifacts.

The configured RAMNV2 adapter consumes numerical 10-second waveforms, not
photos or screenshots of ECG paper.  JPEG files are therefore accepted only
as an authorized source-image record and are never passed to the waveform
parser or model.
"""
from __future__ import annotations

from io import BytesIO
import warnings

from PIL import Image, UnidentifiedImageError

from .security import APIError


# A 4k-by-3k clinical photo is supported while bounding decoder memory use.
# The request-size limit is enforced separately by Flask/application settings.
MAX_ECG_IMAGE_PIXELS = 12_000_000
JPEG_MAGIC = b"\xff\xd8\xff"


def validate_jpeg_image(payload: bytes) -> tuple[int, int]:
    """Decode and validate a JPEG upload without trusting its name or MIME type.

    The original bytes are retained as the clinical source artifact.  This
    function does not strip EXIF data or transcode the image; a separately
    governed derivative would be needed before browser display if metadata
    removal is required.
    """
    if not payload.startswith(JPEG_MAGIC):
        raise APIError("INVALID_ECG_IMAGE", "The .jpg/.jpeg upload is not a valid JPEG image.", 400)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(payload)) as image:
                if image.format != "JPEG":
                    raise ValueError("decoded format is not JPEG")
                width, height = image.size
                if width < 1 or height < 1 or width * height > MAX_ECG_IMAGE_PIXELS:
                    raise ValueError("image dimensions exceed the allowed limit")
                image.verify()

            # ``verify`` checks container structure; reopening and loading
            # catches truncated JPEG scan data without trusting the suffix.
            with Image.open(BytesIO(payload)) as image:
                if image.format != "JPEG" or image.size != (width, height):
                    raise ValueError("JPEG metadata changed during validation")
                image.load()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning,
            UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise APIError("INVALID_ECG_IMAGE", "The .jpg/.jpeg upload is not a valid safe JPEG image.", 400) from exc
    return width, height
