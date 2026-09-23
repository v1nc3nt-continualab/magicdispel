"""Image formats rebuilt from an allowlist of the parts needed to show them.

Each format module provides:
    rebuild(data) -> bytes      a new file holding only the allowlisted parts
    verify(original, rebuilt)   an independent check of the result; raises
                                VerificationError if anything is off

Formats not listed in REBUILT still go through the older ExifTool pipeline
in core.py while they are migrated.
"""
from . import bmp, gif, jpeg, png, webp

REBUILT = {"PNG": png, "APNG": png, "BMP": bmp, "JPEG": jpeg, "WEBP": webp, "GIF": gif}


def identify(data):
    """The format name from a file's leading bytes, or None if not rebuilt yet."""
    if data.startswith(png.SIGNATURE):
        return "APNG" if png.is_animated(data) else "PNG"
    if data[:3] == b"\xff\xd8\xff":
        return "JPEG"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "GIF"
    if data[:2] == b"BM":
        return "BMP"
    return None
