"""BMP: saved as a lossless PNG with the same pixels, color profile and DPI."""
import io

from PIL import Image

from .. import pixels
from ..errors import FormatError
from . import png

MODES = {"1", "L", "P", "RGB", "RGBA"}
INFO_HEADER, RLE4 = 40, 2  # the smallest header that has a compression method, and the method of 4 bits per pixel
# The header that can hold a profile, whose fields start after the 14-byte file header, and its color space
# types that name one: 'MBED', a profile in the file, and 'LINK', one in a file of its own.
V5_HEADER, EMBEDDED, LINKED = 124, 0x4D424544, 0x4C494E4B


def rebuild(data):
    # Pillow decodes most such files wrongly, and its pixels are what the result is checked against.
    if len(data) >= 34 and field(data, 14) >= INFO_HEADER and field(data, 30) == RLE4:
        raise FormatError("unsupported_variant", format="BMP (4-bit run-length)")
    try:
        encoded = as_png(data)
    except FormatError:
        raise
    except Exception:  # Pillow's decoders fail in many ways
        raise FormatError("undecodable", format="BMP")
    return png.rebuild(encoded)


def verify(original, rebuilt):
    """The result must be a clean PNG. Its pixels are compared with the BMP's
    as for every format Pillow decodes (see pixels.py)."""
    png.check_structure(rebuilt)


def embedded_profile(data):
    """The ICC profile a version 5 header embeds, or None. Pillow does not read it, and without it
    the colors would change; one that is linked, by the name of a file, cannot be kept."""
    if len(data) < 14 + V5_HEADER or field(data, 14) < V5_HEADER:
        return None
    if field(data, 14 + 56) == LINKED:
        raise FormatError("unsupported_variant", format="BMP (linked color profile)")
    if field(data, 14 + 56) != EMBEDDED:
        return None
    start, size = 14 + field(data, 14 + 112), field(data, 14 + 116)  # counted from the header
    if not size or start + size > len(data):
        raise FormatError("damaged", format="BMP")
    return data[start:start + size]


def field(data, offset):
    return int.from_bytes(data[offset:offset + 4], "little")


def as_png(data):
    with pixels.opened(data, "BMP") as picture:
        if picture.format != "BMP" or picture.mode not in MODES:
            raise FormatError("unsupported_variant", format="BMP")
        picture.load()
        # A new image made from the pixels alone, so nothing else can carry over.
        fresh = Image.frombytes(picture.mode, picture.size, picture.tobytes())
        if picture.mode == "P":
            fresh.putpalette(picture.getpalette())
        options = {key: picture.info[key] for key in ("icc_profile", "transparency", "dpi")
                   if key in picture.info}
        profile = embedded_profile(data)
        if profile:
            options["icc_profile"] = profile
        encoded = io.BytesIO()
        fresh.save(encoded, "PNG", **options)
    return encoded.getvalue()
