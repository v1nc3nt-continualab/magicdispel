"""What Pillow decodes from a file, for comparing a result with its original."""
import hashlib
import io
import warnings
from contextlib import contextmanager

from PIL import Image, features

from .errors import FormatError, VerificationError

# Formats Pillow decodes (AVIF only when built with it). For HEIC, heif.verify
# compares every retained image item byte for byte instead.
FORMATS = {"PNG", "APNG", "BMP", "JPEG", "WEBP", "GIF", "AVIF", "TIFF"}
# One limit for every format, with room for 200-megapixel phone photos. Pillow
# refuses images over twice MAX_IMAGE_PIXELS as possible decompression bombs,
# and only warns between the two.
MEGAPIXELS = 268
Image.MAX_IMAGE_PIXELS = MEGAPIXELS * 1_000_000 // 2
warnings.filterwarnings("ignore", category=Image.DecompressionBombWarning)
STRIP = 256  # rows hashed at a time, so no frame is copied whole


def decodes(kind):
    return kind in FORMATS and (kind != "AVIF" or features.check("avif"))


@contextmanager
def opened(data, kind):
    """The image in `data`, opened by Pillow; FormatError if it is too large."""
    try:
        with Image.open(io.BytesIO(data)) as picture:
            yield picture
    except Image.DecompressionBombError:
        raise FormatError("too_large", format=kind, limit=MEGAPIXELS)


def compare(original, rebuilt, kind):
    """Pillow must decode the same frames, timing and transparency from both."""
    try:
        before = digest(original, kind)
    except FormatError:
        raise
    except (OSError, SyntaxError, ValueError):
        # Without a decodable original there is nothing to compare against.
        raise FormatError("damaged", format=kind)
    try:
        after = digest(rebuilt, kind)
    except (OSError, SyntaxError, ValueError):
        raise VerificationError("verification_failed", detail="the result cannot be decoded")
    if before != after:
        raise VerificationError("pixels_changed")


def digest(data, kind):
    """A digest of every displayed frame: its pixels as decoded, their mode and
    palette, timing, transparency and the repeat count."""
    result = hashlib.sha256()
    with opened(data, kind) as picture:
        frames = getattr(picture, "n_frames", 1)
        result.update(repr((picture.size, frames, picture.info.get("loop"),
                            picture.info.get("default_image", False))).encode())
        for index in range(frames):
            picture.seek(index)
            picture.load()
            result.update(repr((picture.size, picture.mode, picture.info.get("duration", 0),
                                picture.info.get("transparency"),
                                picture.getpalette() if picture.mode in ("P", "PA") else None)).encode())
            width, height = picture.size
            for top in range(0, height, STRIP):
                result.update(picture.crop((0, top, width, min(top + STRIP, height))).tobytes())
    return result.digest()
