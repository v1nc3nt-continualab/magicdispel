"""Image formats rebuilt from an allowlist of the parts needed to show them.

Each format module provides:
    rebuild(data) -> bytes      a new file holding only the allowlisted parts;
                                raises FormatError if that cannot be done safely
    verify(original, rebuilt)   an independent check of the result; raises
                                VerificationError if anything is off
and, for a file of several pictures that decoders show in different ways:
    pictures(data) -> [bytes]   the pictures a clean copy keeps, each as a file
                                of its own, to compare one by one
"""
from . import bmp, gif, heif, jpeg, png, tiff, webp

MODULES = {"PNG": png, "APNG": png, "BMP": bmp, "JPEG": jpeg, "WEBP": webp, "GIF": gif,
           "HEIC": heif, "AVIF": heif, "TIFF": tiff}
# File name extensions of each format, the first being the default. A source
# whose extension does not match its content gets the default; BMP becomes PNG.
SUFFIXES = {"JPEG": (".jpg", ".jpeg", ".jpe"), "PNG": (".png",), "APNG": (".png", ".apng"),
            "HEIC": (".heic", ".heif", ".hif"), "AVIF": (".avif",), "WEBP": (".webp",),
            "GIF": (".gif",), "TIFF": (".tiff", ".tif"), "BMP": (".png",)}


def identify(data):
    """The format name from a file's leading bytes, or None for other files."""
    if data[4:8] == b"ftyp":
        return heif.brand_format(data)
    if data.startswith(png.SIGNATURE):
        return "APNG" if png.is_animated(data) else "PNG"
    if data[:3] == b"\xff\xd8\xff":
        return "JPEG"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "GIF"
    if data[:4] in (b"II*\0", b"MM\0*", b"II+\0", b"MM\0+"):  # the last two are BigTIFF
        return "TIFF"
    if data[:2] == b"BM":
        return "BMP"
    return None
