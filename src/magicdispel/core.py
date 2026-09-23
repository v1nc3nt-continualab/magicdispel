"""Remove photo metadata locally. BMP is converted losslessly to PNG."""

import errno
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile

from . import formats
from .errors import FormatError, InputError, VerificationError


FORMATS = {
    "JPEG": (".jpg", ".jpeg", ".jpe"),
    "PNG": (".png",),
    "APNG": (".png", ".apng"),
    "HEIC": (".heic", ".heif", ".hif"),
    "AVIF": (".avif",),
    "WEBP": (".webp",),
    "GIF": (".gif",),
    "TIFF": (".tiff", ".tif"),
    "BMP": (".png",),
}
TYPE_ALIASES = {"HEIF": "HEIC", "Extended WEBP": "WEBP", "WEBP (lossless)": "WEBP",
                "Extended WEBP (lossless)": "WEBP"}
DISPLAY_EXIF = {
    "Orientation", "ColorSpace", "Gamma", "InteropIndex", "ExifVersion",
    "FlashpixVersion", "ComponentsConfiguration", "YCbCrPositioning",
    "XResolution", "YResolution", "ResolutionUnit",
}
# TIFF stores image structure in EXIF IFDs. These are needed to decode pixels;
# private text fields in those same directories must be deleted individually.
TIFF_STRUCTURE = {
    "ImageWidth", "ImageHeight", "BitsPerSample", "Compression",
    "PhotometricInterpretation", "Thresholding", "FillOrder", "StripOffsets",
    "SamplesPerPixel", "RowsPerStrip", "StripByteCounts", "PlanarConfiguration",
    "T4Options", "T6Options", "Group3Options", "Group4Options", "PageNumber",
    "TransferFunction", "WhitePoint", "PrimaryChromaticities", "ColorMap",
    "HalftoneHints", "TileWidth", "TileLength", "TileOffsets", "TileByteCounts",
    "ExtraSamples", "SampleFormat", "SMinSampleValue", "SMaxSampleValue",
    "MinSampleValue", "MaxSampleValue", "JPEGTables", "JPEGProc",
    "YCbCrCoefficients", "YCbCrSubSampling", "ReferenceBlackWhite", "Predictor",
    "InkSet", "NumberOfInks", "NewSubfileType", "SubfileType",
    "PreviewImageStart", "PreviewImageLength", "PreviewImage",
}
PRIVATE_GROUPS = {"XMP", "IPTC", "MakerNotes", "Photoshop", "GPS"}
PRIVATE_TAG = re.compile(
    r"gps|latitude|longitude|location|serialnumber|ownername|"
    r"^(?:make|model|artist|author|creator|comment|description|imagedescription|"
    r"copyright|software|datetimeoriginal|createdate|modifydate|creationdate|"
    r"creationtime|trackcreatedate|trackmodifydate|mediacreatedate|mediamodifydate)$",
    re.IGNORECASE,
)
# Editing-only HEIF auxiliaries are removed before metadata cleanup. Only known
# numeric HDR fields may remain; camera calibration and portrait data may not.
HEIF_NUMERIC_XMP = {
    "XMP-HDRGainMap": {"HDRGainMapVersion", "HDRGainMapHeadroom"},
    "XMP-apdi": {"IntMaxValue", "IntMinValue", "FloatMaxValue", "FloatMinValue",
                 "StoredFormat", "NativeFormat"},
}
HEIF_ENUM_XMP = {
    ("XMP-apdi", "AuxiliaryImageType"): {"urn:com:apple:photo:2020:aux:hdrgainmap"},
}


class CleanError(Exception):
    pass


def find_exiftool():
    """ExifTool for the optional second check, or None when it is not installed.
    An explicit MAGICDISPEL_EXIFTOOL that does not work is an error."""
    configured = os.environ.get("MAGICDISPEL_EXIFTOOL")
    if configured:
        candidate = shutil.which(configured) or os.path.expanduser(configured)
        if Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
        raise CleanError("MAGICDISPEL_EXIFTOOL is not an executable file: " + configured)
    for candidate in (shutil.which("exiftool"), "/opt/homebrew/bin/exiftool",
                      "/usr/local/bin/exiftool"):
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def exiftool_version(exiftool):
    return run(exiftool, ["-ver"]).stdout.decode("utf-8", "replace").strip()


def supported_exiftool(version):
    """12.73 is the oldest release that reads recent iPhone files reliably."""
    match = re.fullmatch(r"(\d+)\.(\d+)(?:_\d+)?", version)
    return bool(match) and tuple(map(int, match.groups())) >= (12, 73)


def check_dependency(exiftool):
    version = exiftool_version(exiftool)
    if not supported_exiftool(version):
        raise CleanError("ExifTool 12.73 or newer is required (found " + version + ")")
    return version

def run(exiftool, args, cwd=None):
    result = subprocess.run(
        [exiftool, "-config", "", "-charset", "filename=UTF8"] + args,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).decode("utf-8", "replace").strip()
        raise CleanError(detail or "ExifTool failed")
    return result


def clear_output_attributes(path):
    # Only the verified byte stream is copied, not source xattrs, resource forks
    # or NTFS alternate streams. Remove attributes on the new file when supported.
    if sys.platform == "darwin":
        result = subprocess.run(
            ["/usr/bin/xattr", "-c", str(path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if result.returncode:
            raise CleanError("Could not clear output extended attributes: " +
                             result.stderr.decode("utf-8", "replace").strip())
    elif hasattr(os, "listxattr") and hasattr(os, "removexattr"):
        try:
            for attribute in os.listxattr(path):
                if attribute.startswith("user."):
                    os.removexattr(path, attribute)
        except OSError as error:
            if error.errno not in {errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS}:
                raise

def inspect(exiftool, path):
    result = run(exiftool, [
        "-j", "-G0:1:4", "-a", "-s", "-n", "-e", "-u",
        "-api", "ImageHashType=SHA256", "-all", "-ImageDataHash", str(path),
    ])
    data = json.loads(result.stdout)[0]
    for key, value in data.items():
        if key.split(":")[-1] in {"Error", "Warning"}:
            raise CleanError(str(value))
    return data


def value_for(data, name):
    return next((value for key, value in data.items()
                 if key.split(":")[-1] == name), None)


def image_type(data):
    name = value_for(data, "FileType")
    if name == "MP4" and value_for(data, "MajorBrand") == "avis":
        return "AVIF"
    return TYPE_ALIASES.get(name, name)


def pillow_image():
    try:
        from PIL import Image
        return Image
    except ImportError:
        raise CleanError("这种格式需要 Pillow：python3 -m pip install Pillow")


def decoded_snapshot(path):
    """Check every displayed frame, transparency, timing and repeat count."""
    Image = pillow_image()
    digest = hashlib.sha256()
    with Image.open(path) as picture:
        header = (picture.size, getattr(picture, "n_frames", 1),
                  picture.info.get("loop"), picture.info.get("default_image", False))
        digest.update(repr(header).encode())
        for index in range(getattr(picture, "n_frames", 1)):
            picture.seek(index)
            picture.load()
            digest.update(repr((picture.size, picture.info.get("duration", 0))).encode())
            digest.update(picture.convert("RGBA").tobytes())
            if picture.format == "TIFF":
                digest.update(picture.mode.encode())
                digest.update(picture.tobytes())
    return digest.digest()


def file_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.digest()


def rendering_xmp(key, value, file_type):
    parts = key.split(":")
    if file_type == "JPEG" and len(parts) >= 3:
        namespace, tag = parts[1], parts[-1]
        def numbers(item):
            values = item if isinstance(item, list) else [item]
            try:
                return (len(values) in {1, 3} and all(not isinstance(v, bool) and
                        math.isfinite(float(v)) and abs(float(v)) <= 1e12 for v in values))
            except (TypeError, ValueError, OverflowError):
                return False
        if parts[0] == "MakerNotes" and namespace == "Apple":
            return tag in {"HDRHeadroom", "HDRGain"} and numbers(value)
        if parts[0] != "XMP":
            return False
        if namespace == "XMP-hdrgm":
            if tag == "BaseRenditionIsHDR":
                return value in (True, False, 0, 1, "0", "1", "True", "False")
            return tag in {"Version", "GainMapMin", "GainMapMax", "Gamma", "OffsetSDR",
                           "OffsetHDR", "HDRCapacityMin", "HDRCapacityMax", "BaseHeadroom",
                           "AlternateHeadroom"} and numbers(value)
        if namespace in {"XMP-apdi", "XMP-HDRGainMap"}:
            if tag == "AuxiliaryImageType":
                return value == "urn:com:apple:photo:2020:aux:hdrgainmap"
            return tag in {"NativeFormat", "StoredFormat", "HDRGainMapVersion",
                           "HDRGainMapHeadroom"} and numbers(value)
        return False
    if file_type not in {"HEIC", "AVIF"} or len(parts) < 3 or parts[0] != "XMP":
        return False
    namespace, tag = parts[1], parts[-1]

    def numeric(item):
        if isinstance(item, bool) or not isinstance(item, (int, float, str)):
            return False
        try:
            return math.isfinite(float(item)) and abs(float(item)) <= 1e12
        except (ValueError, OverflowError):
            return False

    if tag in HEIF_NUMERIC_XMP.get(namespace, set()):
        return numeric(value)
    if (namespace, tag) in HEIF_ENUM_XMP:
        return isinstance(value, str) and value in HEIF_ENUM_XMP[namespace, tag]
    return False


def verify_metadata(after):
    file_type = image_type(after)
    allowed_exif = DISPLAY_EXIF | (TIFF_STRUCTURE if file_type == "TIFF" else set())
    remaining = []
    for key, value in after.items():
        parts = key.split(":")
        group, tag = parts[0], parts[-1]
        # ICC identity data is sanitized and verified as complete profile blocks.
        if (group in {"SourceFile", "ExifTool", "ICC_Profile", "Composite"}
                or parts[:2] == ["File", "System"]):
            continue
        if rendering_xmp(key, value, file_type):
            continue
        if (group in PRIVATE_GROUPS or group == "PLIST" or
                (group == "EXIF" and tag not in allowed_exif) or
                (group != "EXIF" and PRIVATE_TAG.search(tag)
                 and value not in (None, "", 0, "0000:00:00 00:00:00"))):
            remaining.append(key)
    if remaining:
        raise VerificationError("metadata_remains", tags=", ".join(remaining))


UNKNOWN_TAG = re.compile(r"^Unknown|_0x[0-9a-f]{4}$", re.IGNORECASE)
# HEIF/AVIF structure that ExifTool lists as unknown boxes: sequence edit lists,
# codec settings, color, coding constraints, alternative-image groups, item
# data, and emptied space. formats/heif.py verifies these boxes itself.
BMFF_STRUCTURE = {"Unknown_" + name for name in ("edts", "av1C", "hvcC", "colr", "ccst", "pasp",
                                                  "btrt", "free", "altr", "idat")}


def cross_check(exiftool, folder, data, kind):
    """ExifTool's independent reading of a rebuilt file: no warnings, nothing
    private, and no data it cannot identify."""
    with tempfile.TemporaryDirectory(prefix=".magicdispel-", dir=folder) as temp:
        path = Path(temp) / ("check" + FORMATS[kind][0])
        path.write_bytes(data)
        try:
            after = inspect(exiftool, path)
        except CleanError as error:
            raise VerificationError("exiftool_problem", detail=str(error))
    unknown = [key for key in after if UNKNOWN_TAG.search(key.split(":")[-1])
               and not (key.startswith("QuickTime:") and key.split(":")[-1] in BMFF_STRUCTURE)]
    if unknown:
        raise VerificationError("metadata_remains", tags=", ".join(unknown))
    verify_metadata(after)


def pillow_decodes(kind):
    if kind not in formats.PILLOW_DECODES:
        return False
    from PIL import features
    return kind != "AVIF" or features.check("avif")


def clean(exiftool, argument, anonymous=False):
    """Save a cleaned copy of a photo next to it and return the copy's path.

    The format's module rebuilds the file from what is needed to show it and
    checks the result on its own; Pillow must decode identical frames where it
    can; ExifTool, when installed, gives an independent second reading.
    Nothing is saved unless every check passes.
    """
    source = Path(os.path.abspath(os.path.expanduser(argument)))
    if not source.is_file():
        raise InputError("not_a_file", path=source)
    data = source.read_bytes()
    kind = formats.identify(data)
    if kind is None:
        raise InputError("unsupported_format")
    module = formats.REBUILT[kind]
    rebuilt = module.rebuild(data)
    module.verify(data, rebuilt)
    if pillow_decodes(kind):
        try:
            original_pixels = decoded_snapshot(io.BytesIO(data))
        except (OSError, SyntaxError, ValueError):
            # Without a decodable original there is nothing to compare against.
            raise FormatError("damaged", format=kind)
        try:
            rebuilt_pixels = decoded_snapshot(io.BytesIO(rebuilt))
        except (OSError, SyntaxError, ValueError):
            raise VerificationError("verification_failed", detail="the result cannot be decoded")
        if original_pixels != rebuilt_pixels:
            raise VerificationError("pixels_changed")
    if exiftool:
        cross_check(exiftool, source.parent, rebuilt, kind)
    if hashlib.sha256(data).digest() != file_digest(source):
        raise VerificationError("source_changed")
    suffixes = FORMATS[kind]
    suffix = source.suffix if source.suffix.lower() in suffixes else suffixes[0]
    return publish(rebuilt, source, suffix, anonymous=anonymous)


def publish(data, source, suffix, anonymous=False):
    """Write data next to source under a new name; never overwrite anything."""
    index = 0
    while True:
        tail = "_clean" if index == 0 else "_clean_" + str(index)
        name = ("photo_" + secrets.token_hex(16) + suffix.lower() if anonymous
                else source.stem + tail + suffix)
        dest = source.with_name(name)
        try:
            output = dest.open("xb")
        except FileExistsError:
            index += 1
            continue
        try:
            with output:
                output.write(data)
            clear_output_attributes(dest)
            return dest
        except BaseException:
            dest.unlink(missing_ok=True)
            raise


