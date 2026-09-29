"""What Pillow decodes from a file, for comparing a result with its original."""
import hashlib
import io
import os
import sys
import warnings
from contextlib import contextmanager

from PIL import Image, features

from .errors import FormatError, VerificationError

# Formats Pillow decodes (AVIF only when built with it). For HEIC, heif.verify
# compares every retained image item byte for byte instead.
FORMATS = {"PNG", "APNG", "BMP", "JPEG", "WEBP", "GIF", "AVIF", "TIFF"}
# The one decoder that opens each kind. Pillow would otherwise open a file by
# any of its many decoders whose signature the file's bytes match, and each
# decoder is more code that a file made for it could reach.
DECODERS = {"PNG": ["PNG"], "APNG": ["PNG"], "BMP": ["BMP"], "JPEG": ["JPEG"], "WEBP": ["WEBP"], "GIF": ["GIF"],
            "AVIF": ["AVIF"], "TIFF": ["TIFF"]}
RESULTS = {"BMP": "PNG"}  # a kind whose clean copy is of another
# One limit for every format, with room for 200-megapixel phone photos. Pillow
# refuses images over twice MAX_IMAGE_PIXELS as possible decompression bombs,
# and only warns between the two.
MEGAPIXELS = 268
Image.MAX_IMAGE_PIXELS = MEGAPIXELS * 1_000_000 // 2
warnings.filterwarnings("ignore", category=Image.DecompressionBombWarning)
# Pillow warns about damaged files it can partly read; the comparison decides.
warnings.filterwarnings("ignore", category=UserWarning, module=r"PIL\.")
STRIP = 256  # rows hashed at a time, so no frame is copied whole
# The most pixels a file may ask Pillow to decode, counted over every frame: about a minute's work.
# A canvas of tens of megapixels takes a header, and a number of frames little more.
FRAME_PIXELS = 4 << 30
# The largest canvas of an animation: Pillow holds several copies of it, so a header for 250 megapixels
# asks for 8 GB, and no animation is so large (an 8K one is 33).
ANIMATION_MEGAPIXELS = 64
ONE_CANVAS = {"WEBP", "GIF", "APNG", "AVIF"}  # kinds whose frames are all drawn on the file's canvas
MAX_FRAMES = {"WEBP": 32768}  # libwebp's time grows with the square of them: 100,000 frames of a pixel take 15 seconds


def decodes(kind):
    return kind in FORMATS and (kind != "AVIF" or features.check("avif"))


@contextmanager
def opened(data, kind):
    """The image in `data`, opened by Pillow; FormatError if it is too large."""
    try:
        with Image.open(io.BytesIO(data), formats=DECODERS[kind]) as picture:
            yield picture
    except Image.DecompressionBombError:
        raise FormatError("too_large", format=kind, limit=MEGAPIXELS)


def compare(original, rebuilt, kind):
    """Pillow must decode the same frames, timing and transparency from both.
    Pillow's decoders fail in many ways (OSError, SyntaxError, RuntimeError and
    more); any failure means the file cannot be checked."""
    try:
        before = digest(original, kind)
    except FormatError:
        raise
    except Exception:
        # Without a decodable original there is nothing to compare against.
        raise FormatError("undecodable", format=kind)
    try:
        after = digest(rebuilt, RESULTS.get(kind, kind))
    except Exception:
        raise VerificationError("verification_failed", detail="the result cannot be decoded")
    if before != after:
        raise VerificationError("pixels_changed")


def digest(data, kind):
    """A digest of every displayed frame: its pixels as decoded, their mode and
    palette, timing, transparency and the repeat count."""
    result = hashlib.sha256()
    with quiet(), opened(data, kind) as picture:
        frames = getattr(picture, "n_frames", 1)
        if frames > MAX_FRAMES.get(kind, frames):
            raise FormatError("too_many_frames", format=kind, limit=MAX_FRAMES[kind])
        if frames > 1 and picture.size[0] * picture.size[1] > ANIMATION_MEGAPIXELS * 1_000_000:
            raise FormatError("too_large", format=kind, limit=ANIMATION_MEGAPIXELS)
        if kind in ONE_CANVAS and frames * picture.size[0] * picture.size[1] > FRAME_PIXELS:
            raise too_many_pixels(kind)
        result.update(repr((picture.size, frames, picture.info.get("loop"),
                            picture.info.get("default_image", False))).encode())
        decoded = 0
        for index in range(frames):
            picture.seek(index)
            picture.load()
            result.update(repr((picture.size, picture.mode, picture.info.get("duration", 0),
                                picture.info.get("transparency"),
                                picture.getpalette() if picture.mode in ("P", "PA") else None)).encode())
            width, height = picture.size
            decoded += width * height
            if decoded > FRAME_PIXELS:
                raise too_many_pixels(kind)
            for top in range(0, height, STRIP):
                result.update(picture.crop((0, top, width, min(top + STRIP, height))).tobytes())
    return result.digest()


@contextmanager
def quiet():
    """Standard error muted while Pillow decodes: libtiff writes its complaints
    about a damaged file straight to it, in front of what the command says."""
    try:
        sys.stderr.flush()
        saved, null = os.dup(2), os.open(os.devnull, os.O_WRONLY)
    except (AttributeError, OSError, ValueError):  # no standard error to mute
        yield
        return
    try:
        os.dup2(null, 2)
        yield
    finally:
        os.dup2(saved, 2)
        os.close(saved)
        os.close(null)


def too_many_pixels(kind):
    return FormatError("too_many_pixels", format=kind, limit=FRAME_PIXELS >> 30)
