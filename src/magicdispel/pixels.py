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
# Pillow warns about damaged files it can partly read; the comparison decides.
warnings.filterwarnings("ignore", category=UserWarning, module=r"PIL\.")
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


def compare(original, rebuilt, kind, frames=None):
    """Pillow must decode the same frames, timing and transparency from both;
    `frames` lists the original's frames the result keeps, when not all.
    Pillow's decoders fail in many ways (OSError, SyntaxError, RuntimeError and
    more); any failure means the file cannot be checked."""
    try:
        before = digest(original, kind, frames)
    except FormatError:
        raise
    except Exception:
        # Without a decodable original there is nothing to compare against.
        raise FormatError("undecodable", format=kind)
    try:
        after = digest(rebuilt, kind)
    except Exception:
        raise VerificationError("verification_failed", detail="the result cannot be decoded")
    if before != after:
        raise VerificationError("pixels_changed")


def digest(data, kind, frames=None):
    """A digest of every displayed frame, or of the given ones: its pixels as
    decoded, their mode and palette, timing, transparency and the repeat count.
    Pillow may show fewer frames than a file holds (an Ultra HDR JPEG as its
    primary image alone, without the gain map); only those are given."""
    result = hashlib.sha256()
    with opened(data, kind) as picture:
        shown = getattr(picture, "n_frames", 1)
        frames = range(shown) if frames is None else [index for index in frames if index < shown]
        result.update(repr((picture.size, len(frames), picture.info.get("loop"),
                            picture.info.get("default_image", False))).encode())
        for index in frames:
            picture.seek(index)
            picture.load()
            result.update(repr((picture.size, picture.mode, picture.info.get("duration", 0),
                                picture.info.get("transparency"),
                                picture.getpalette() if picture.mode in ("P", "PA") else None)).encode())
            width, height = picture.size
            for top in range(0, height, STRIP):
                result.update(picture.crop((0, top, width, min(top + STRIP, height))).tobytes())
    return result.digest()
