"""BMP: saved as a lossless PNG with the same pixels, color profile and DPI."""
import io

from PIL import Image

from ..errors import FormatError, VerificationError
from . import png

MODES = {"1", "L", "P", "RGB", "RGBA"}


def rebuild(data):
    with Image.open(io.BytesIO(data)) as picture:
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
    return png.rebuild(encoded.getvalue())


def verify(original, rebuilt):
    png.check_structure(rebuilt)
    if pixels(original) != pixels(rebuilt):
        raise VerificationError("verification_failed", detail="PNG pixels differ from the BMP")


def pixels(data):
    with Image.open(io.BytesIO(data)) as picture:
        palette = picture.getpalette() if picture.mode == "P" else None
        return picture.mode, picture.size, palette, picture.info.get("transparency"), picture.tobytes()
