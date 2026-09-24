"""ExifTool as an optional second opinion.

When ExifTool 12.73 or newer is installed, every rebuilt file is read by it
before being saved. Any warning, any field outside the display allowlist and
any data ExifTool cannot identify stops the save. ExifTool reads a photo from
a pipe: no file is written, and no path, which Windows would pass in its
legacy code page and ExifTool might misread, is involved. A video, cleaned in
a file of its own, is read from that file, whose path ExifTool gets in UTF-8
on its standard input: through a pipe, ExifTool would hold the whole video in
memory to move through it.
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from . import xmp
from .errors import UserError, VerificationError
from .formats import mp4

# Display fields a clean file may hold, as ExifTool names them. TIFF keeps its
# decoding tags in the same EXIF group.
DISPLAY_EXIF = {
    "Orientation", "ColorSpace", "Gamma", "InteropIndex", "ExifVersion", "FlashpixVersion",
    "ComponentsConfiguration", "YCbCrPositioning", "XResolution", "YResolution", "ResolutionUnit",
}
TIFF_STRUCTURE = {
    "ImageWidth", "ImageHeight", "BitsPerSample", "Compression", "PhotometricInterpretation",
    "Thresholding", "FillOrder", "StripOffsets", "SamplesPerPixel", "RowsPerStrip",
    "StripByteCounts", "PlanarConfiguration", "T4Options", "T6Options", "Group3Options",
    "Group4Options", "PageNumber", "TransferFunction", "WhitePoint", "PrimaryChromaticities",
    "ColorMap", "HalftoneHints", "TileWidth", "TileLength", "TileOffsets", "TileByteCounts",
    "ExtraSamples", "SampleFormat", "SMinSampleValue", "SMaxSampleValue", "MinSampleValue",
    "MaxSampleValue", "JPEGTables", "JPEGProc", "YCbCrCoefficients", "YCbCrSubSampling",
    "ReferenceBlackWhite", "Predictor", "InkSet", "NumberOfInks", "NewSubfileType", "SubfileType",
    # ExifTool's names for the strip offsets of JPEG-compressed TIFF
    "PreviewImageStart", "PreviewImageLength", "PreviewImage",
}
PRIVATE_GROUPS = {"XMP", "IPTC", "MakerNotes", "Photoshop", "GPS", "PLIST"}
PRIVATE_TAG = re.compile(
    r"gps|latitude|longitude|location|serialnumber|ownername|"
    r"^(?:make|model|artist|author|creator|comment|description|imagedescription|"
    r"copyright|software|datetimeoriginal|createdate|modifydate|creationdate|"
    r"creationtime|trackcreatedate|trackmodifydate|mediacreatedate|mediamodifydate)$",
    re.IGNORECASE)
UNKNOWN_TAG = re.compile(r"^Unknown|_0x[0-9a-f]{4}$", re.IGNORECASE)
# HEIF/AVIF and video structure that ExifTool lists as unknown boxes: edit
# lists, codec settings, color, coding constraints, alternative-image groups,
# item data, emptied space, and the other boxes a clean video keeps.
# formats/heif.py and formats/mp4.py verify these boxes themselves.
BMFF_STRUCTURE = ({"Unknown_" + name for name in ("edts", "av1C", "hvcC", "colr", "ccst", "pasp",
                                                   "btrt", "free", "altr", "idat")}
                  | {"Unknown_" + kind.decode("latin-1") for kind in mp4.STRUCTURE})
# ExifTool's group names for the XMP namespaces whose HDR fields xmp.py keeps.
XMP_GROUPS = {"XMP-hdrgm": xmp.ADOBE_GAIN_MAP, "XMP-apdi": xmp.APPLE_PIXEL_DATA,
              "XMP-HDRGainMap": xmp.APPLE_GAIN_MAP}


class ExifToolError(Exception):
    """ExifTool could not run, or reported a problem with a file."""


def find():
    """The ExifTool executable, or None when it is not installed. An explicit
    MAGICDISPEL_EXIFTOOL that does not name a program is an error."""
    configured = os.environ.get("MAGICDISPEL_EXIFTOOL")
    if configured:
        candidate = shutil.which(configured) or os.path.expanduser(configured)
        if Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
        raise UserError("exiftool_misconfigured", path=configured)
    for candidate in (shutil.which("exiftool"), "/opt/homebrew/bin/exiftool", "/usr/local/bin/exiftool"):
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def version(exiftool):
    """ExifTool's version, or None if it does not run."""
    try:
        return run(exiftool, ["-ver"]).stdout.decode("utf-8", "replace").strip()
    except (ExifToolError, OSError):
        return None


def usable(found_version):
    """12.73 is the oldest release that reads recent iPhone files reliably."""
    match = re.fullmatch(r"(\d+)\.(\d+)(?:_\d+)?", found_version or "")
    return bool(match) and tuple(map(int, match.groups())) >= (12, 73)


def second_opinion(exiftool, data, kind, path=None):
    """Have ExifTool read a rebuilt file, `data` or the file at `path`; raise
    VerificationError on anything off."""
    try:
        tags = read(exiftool, data, path)
    except ExifToolError as error:
        raise VerificationError("exiftool_problem", detail=str(error))
    check_tags(tags, kind)


def read(exiftool, data, path=None):
    """Every tag ExifTool finds in `data`, or in the file at `path`, keyed
    "Group0:Group1[:CopyN]:Tag"; a warning raises."""
    arguments = ["-j", "-G0:1:4", "-a", "-s", "-n", "-e", "-u", "-all"]
    if path is None:
        result = run(exiftool, arguments + ["-"], data)
    else:
        # The path is an argument file on standard input: see the module docstring.
        result = run(exiftool, arguments + ["-api", "LargeFileSupport=1", "-@", "-"],
                     os.fsdecode(path).encode("utf-8") + b"\n")
    tags = json.loads(result.stdout)[0]
    for key, value in tags.items():
        if key.split(":")[-1] in ("Error", "Warning"):
            raise ExifToolError(str(value))
    return tags


def check_tags(tags, kind):
    """Only structure and display fields may remain, and nothing unidentified."""
    allowed_exif = DISPLAY_EXIF | (TIFF_STRUCTURE if kind == "TIFF" else set())
    remaining = []
    for key, value in tags.items():
        parts = key.split(":")
        group, tag = parts[0], parts[-1]
        # ICC identity data is sanitized and verified as complete profile blocks.
        if group in ("SourceFile", "ExifTool", "ICC_Profile", "Composite") or parts[:2] == ["File", "System"]:
            continue
        if rendering_field(parts, value):
            continue
        if (group in PRIVATE_GROUPS or (group == "EXIF" and tag not in allowed_exif)
                or (group != "EXIF" and PRIVATE_TAG.search(tag)
                    and value not in (None, "", 0, "0000:00:00 00:00:00"))
                or (UNKNOWN_TAG.search(tag) and not (group == "QuickTime" and tag in BMFF_STRUCTURE))):
            remaining.append(key)
    if remaining:
        raise VerificationError("metadata_remains", tags=", ".join(remaining))


def rendering_field(parts, value):
    """HDR rendering values: the gain-map XMP fields xmp.py keeps, and Apple's
    HDR headroom and gain in its maker note."""
    group, family1, tag = parts[0], parts[1] if len(parts) > 2 else "", parts[-1]
    if group == "MakerNotes" and family1 == "Apple":
        return tag in ("HDRHeadroom", "HDRGain") and xmp.numeric(value)
    if group == "XMP" and family1 in XMP_GROUPS:
        text = [str(item) for item in value] if isinstance(value, list) else str(value)
        return xmp.permitted("{%s}%s" % (XMP_GROUPS[family1], tag), text)
    return False


def run(exiftool, arguments, data=None):
    """ExifTool's output for `arguments`, with `data` as its standard input."""
    result = subprocess.run([exiftool, "-config", "", "-charset", "filename=UTF8", *arguments],
                            input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise ExifToolError((result.stderr or result.stdout).decode("utf-8", "replace").strip()
                            or "ExifTool failed")
    return result
