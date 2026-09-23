"""BMP: saved as a lossless PNG with the same pixels, color profile and DPI."""
import io

from PIL import Image

from .. import pixels
from ..errors import FormatError
from . import png

MODES = {"1", "L", "P", "RGB", "RGBA"}


def rebuild(data):
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
        encoded = io.BytesIO()
        fresh.save(encoded, "PNG", **options)
    return encoded.getvalue()
