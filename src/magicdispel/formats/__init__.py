"""Image and video formats rebuilt from an allowlist of the parts needed to show them.

Each format module provides:
    rebuild(data) -> bytes      a new file holding only the allowlisted parts;
                                raises FormatError if that cannot be done safely
    verify(original, rebuilt)   an independent check of the result; raises
                                VerificationError if anything is off
for a file of several pictures that decoders show in different ways:
    pictures(data) -> [bytes]   the pictures a clean copy keeps, each as a file
                                of its own, to compare one by one
and, for videos, which are too large to hold in memory (see video_format):
    clean(original, copy) -> n  clean `copy`, a writable copy of `original`, in
                                place; the result is its first n bytes
"""
from . import bmp, gif, heif, jpeg, mp4, png, tiff, webp

MODULES = {"PNG": png, "APNG": png, "BMP": bmp, "JPEG": jpeg, "WEBP": webp, "GIF": gif,
           "HEIC": heif, "AVIF": heif, "TIFF": tiff, "MP4": mp4, "MOV": mp4}
# File name extensions of each format, the first being the default. A source
# whose extension does not match its content gets the default; BMP becomes PNG.
# MP4 and QuickTime are one family: players may read a file differently by its
# extension, so a video keeps any video extension it has.
VIDEO_SUFFIXES = (".mp4", ".mov", ".m4v", ".qt", ".3gp", ".3g2")
VIDEOS = {"MP4", "MOV"}  # the formats cleaned in a copy of the file (see core.clean_copy)
SUFFIXES = {"JPEG": (".jpg", ".jpeg", ".jpe"), "PNG": (".png",), "APNG": (".png", ".apng"),
            "HEIC": (".heic", ".heif", ".hif"), "AVIF": (".avif",), "WEBP": (".webp",),
            "GIF": (".gif",), "TIFF": (".tiff", ".tif"), "BMP": (".png",),
            "MP4": VIDEO_SUFFIXES, "MOV": (".mov",) + VIDEO_SUFFIXES}


def identify(data):
    """The format name from a file's leading bytes, or None for other files."""
    if data[4:8] == b"ftyp":
        return heif.brand_format(data) or mp4.brand_format(data)
    if data[4:8] in mp4.QUICKTIME_ATOMS:
        return "MOV"
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


def video_format(head):
    """"MP4" or "MOV" from a file's first bytes, or None for other files."""
    return None if heif.brand_format(head) else mp4.brand_format(head)
