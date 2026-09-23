"""Remove photo metadata locally. BMP is converted losslessly to PNG."""

import base64
from collections import Counter
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
import struct
import subprocess
import sys
import tempfile

from . import formats
from .errors import FormatError, VerificationError
from .privacy import (sanitize_icc, sanitize_image_profiles, strip_heif_private,
                      strip_heif_auxiliary)


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
    raise CleanError("ExifTool was not found. Install it from https://exiftool.org/ "
                     "and put exiftool on PATH, or set MAGICDISPEL_EXIFTOOL.")


def check_dependency(exiftool):
    version = run(exiftool, ["-ver"]).stdout.decode("utf-8", "replace").strip()
    match = re.fullmatch(r"(\d+)\.(\d+)(?:_\d+)?", version)
    if not match or tuple(map(int, match.groups())) < (12, 73):
        raise CleanError("ExifTool 12.73 or newer is required (found " + version +
                         "). Version 13.55+ is recommended for recent iPhone HEIC files.")
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
            raise CleanError("图片检查未通过：" + str(value))
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


def tiff_delete_args(before):
    deletes = set()
    for key in before:
        parts = key.split(":")
        if parts[0] != "EXIF" or len(parts) < 3:
            continue
        directory, tag = parts[1], parts[-1]
        if tag not in TIFF_STRUCTURE | DISPLAY_EXIF:
            if not re.fullmatch(r"[A-Za-z0-9_]+", directory + tag):
                raise CleanError("无法安全清理这种 TIFF 字段：" + key)
            deletes.add("-" + directory + ":" + tag + "=")
    args = ["-XMP:All=", "-IPTC:All=", "-Photoshop:All=",
            "-GPS:All=", "-MakerNotes:All=", "-Trailer:All="] + sorted(deletes)
    # Explicitly retain these fields; deleting the last capture field can cause
    # ExifTool to prune an ExifIFD consisting only of default display fields.
    # Avoid -TagsFromFile here: it triggers ExifTool's broken PreviewImage
    # extraction for multi-strip JPEG-compressed TIFFs.
    for key, value in before.items():
        if key.startswith("EXIF:") and key.split(":")[-1] in {"ColorSpace", "Gamma", "InteropIndex"}:
            parts = key.split(":")
            args.append("-" + parts[1] + ":" + parts[-1] + "#=" + str(value))
    return args


def tiff_payload_hash(path):
    """Hash the actual encoded strips/tiles, including every page's JPEG tables."""
    Image = pillow_image()
    digest = hashlib.sha256()
    length = path.stat().st_size
    with Image.open(path) as picture, path.open("rb") as stream:
        for page in range(getattr(picture, "n_frames", 1)):
            picture.seek(page)
            tags = picture.tag_v2
            offsets, sizes = tags.get(273, tags.get(324)), tags.get(279, tags.get(325))
            if offsets is None or sizes is None:
                raise CleanError("无法读取 TIFF 像素数据的位置")
            offsets = offsets if isinstance(offsets, tuple) else (offsets,)
            sizes = sizes if isinstance(sizes, tuple) else (sizes,)
            if len(offsets) != len(sizes):
                raise CleanError("TIFF 像素数据的长度无效")
            digest.update(struct.pack(">II", page, len(offsets)))
            tables = tags.get(347, b"")
            digest.update(struct.pack(">I", len(tables)))
            digest.update(tables)
            for offset, size in zip(offsets, sizes):
                if offset < 0 or size <= 0 or offset + size > length:
                    raise CleanError("TIFF 像素数据越界")
                digest.update(struct.pack(">Q", size))
                stream.seek(offset)
                remaining = size
                while remaining:
                    block = stream.read(min(remaining, 1024 * 1024))
                    if not block:
                        raise CleanError("TIFF 像素数据不完整")
                    digest.update(block)
                    remaining -= len(block)
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


def rendering_snapshot(data, file_type):
    # Instance numbers may change when another XMP packet is removed. Compare
    # the multiset so duplicate render fields are checked without losing any.
    return Counter(
        (key.split(":")[1], key.split(":")[-1], json.dumps(value, sort_keys=True))
        for key, value in data.items()
        if rendering_xmp(key, value, file_type) and not key.endswith(":XMPToolkit")
    )


def verify(before, after, pixels_verified=False, encoding_verified=False):
    file_type = image_type(before)
    original_hash = value_for(before, "ImageDataHash")
    if file_type not in {"GIF", "BMP"} and not encoding_verified and (
            not original_hash or original_hash != value_for(after, "ImageDataHash")):
        raise CleanError("无法确认图片数据保持不变，未导出结果")
    if file_type in {"GIF", "BMP", "APNG", "TIFF", "AVIF"} and not pixels_verified:
        raise CleanError("无法确认所有画面保持不变，未导出结果")
    if image_type(after) != ("PNG" if file_type == "BMP" else file_type):
        raise CleanError("图片格式发生变化，未导出结果")
    if value_for(before, "Orientation") != value_for(after, "Orientation"):
        raise CleanError("图片朝向未能保留，未导出结果")
    if rendering_snapshot(before, file_type) != rendering_snapshot(after, file_type):
        raise CleanError("HDR 或人像显示参数发生变化，未导出结果")
    if file_type == "TIFF":
        def structure(data):
            return Counter((key.split(":")[1], key.split(":")[-1], json.dumps(value))
                           for key, value in data.items()
                           if key.startswith("EXIF:") and key.split(":")[-1] in
                           (TIFF_STRUCTURE | DISPLAY_EXIF) - {"StripOffsets", "TileOffsets", "PreviewImageStart", "PreviewImage"})
        if structure(before) != structure(after):
            raise CleanError("TIFF 画面结构或显示参数发生变化，未导出结果")
    verify_metadata(after)


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
        raise CleanError("仍检测到元数据，未导出结果：" + "、".join(remaining))


UNKNOWN_TAG = re.compile(r"^Unknown|_0x[0-9a-f]{4}$", re.IGNORECASE)


def cross_check(exiftool, folder, data, kind):
    """ExifTool's independent reading of a rebuilt file: no warnings, nothing
    private, and no data it cannot identify."""
    with tempfile.TemporaryDirectory(prefix=".magicdispel-", dir=folder) as temp:
        path = Path(temp) / ("check" + FORMATS[kind][0])
        path.write_bytes(data)
        after = inspect(exiftool, path)
    unknown = [key for key in after if UNKNOWN_TAG.search(key.split(":")[-1])]
    if unknown:
        raise VerificationError("metadata_remains", tags=", ".join(unknown))
    verify_metadata(after)


def clean_rebuilt(exiftool, source, data, kind, anonymous):
    """Formats with their own rebuilder: rebuild, confirm three ways, publish."""
    module = formats.REBUILT[kind]
    rebuilt = module.rebuild(data)
    module.verify(data, rebuilt)
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


def profile_blocks(exiftool, path):
    data = json.loads(run(exiftool, ["-j", "-b", "-a", "-ee3", "-G0:1:3:4",
                                   "-ICC_Profile", str(path)]).stdout)[0]
    return [base64.b64decode(value[7:], validate=True)
            for key, value in data.items() if key.split(":")[-1] == "ICC_Profile"
            and isinstance(value, str) and value.startswith("base64:")]


def verify_profiles(exiftool, before_profiles, target):
    expected = Counter(sanitize_icc(profile) for profile in before_profiles)
    actual = Counter(profile_blocks(exiftool, target))
    if expected != actual:
        raise CleanError("ICC 颜色参数或隐私字段校验失败，未导出结果")


def sanitize_private_metadata(exiftool, target, file_type):
    profiles = profile_blocks(exiftool, target)
    cleaned, count = sanitize_image_profiles(target.read_bytes(), file_type)
    if file_type in {"HEIC", "AVIF"}:
        cleaned = strip_heif_auxiliary(strip_heif_private(cleaned))
    target.write_bytes(cleaned)
    verify_profiles(exiftool, profiles, target)


def clean(exiftool, argument, anonymous=False):
    source = Path(os.path.abspath(os.path.expanduser(argument)))
    if not source.is_file():
        raise CleanError("不是可读取的图片文件：" + str(source))
    data = source.read_bytes()
    kind = formats.identify(data)
    if kind in formats.REBUILT:
        return clean_rebuilt(exiftool, source, data, kind, anonymous)
    # Formats still cleaned with ExifTool while they are migrated.
    before_digest = file_digest(source)
    before = inspect(exiftool, source)
    file_type = image_type(before)
    if file_type not in FORMATS:
        raise CleanError("暂不支持此格式（" + str(file_type) + "）；支持 JPG、PNG、HEIC、AVIF、WebP、GIF、TIFF、BMP")
    if file_type in formats.REBUILT:
        # ExifTool recognized a format whose usual file signature is missing.
        raise FormatError("damaged", format=file_type)
    suffixes = FORMATS[file_type]
    suffix = source.suffix if source.suffix.lower() in suffixes else suffixes[0]
    check_frames = file_type in {"GIF", "APNG", "BMP", "TIFF", "AVIF"}
    original_pixels = decoded_snapshot(source) if check_frames else None
    original_tiff = tiff_payload_hash(source) if file_type == "TIFF" else None
    original_profiles = profile_blocks(exiftool, source)
    with tempfile.TemporaryDirectory(prefix=".magicdispel-", dir=source.parent) as temp:
        staged = Path(temp) / ("clean" + suffixes[0])
        input_path = source
        if file_type in {"HEIC", "AVIF"}:
            # The trimmer verifies that retained image byte ranges are unchanged.
            # Subsequent encoding/profile checks use this trimmed input, since
            # removed auxiliary image payloads intentionally no longer match.
            original = source.read_bytes()
            trimmed = strip_heif_auxiliary(strip_heif_private(original))
            if trimmed != original:
                input_path = Path(temp) / ("source" + suffixes[0])
                input_path.write_bytes(trimmed)
                before = inspect(exiftool, input_path)
                original_profiles = profile_blocks(exiftool, input_path)
        if input_path == source and source.suffix.lower() not in suffixes:
            input_path = Path(temp) / ("source" + suffixes[0])
            shutil.copyfile(source, input_path)
        args = ["-all=", "--ICC_Profile:All", "-Trailer:All=", "-PNG:ModifyDate="]
        if file_type == "TIFF":
            args = tiff_delete_args(before)
        else:
            args += ["-TagsFromFile", "@", "-ColorSpaceTags", "-IFD0:Orientation"]
        if file_type == "AVIF" and value_for(before, "MajorBrand") == "avis":
            for tag in ("CreateDate", "ModifyDate", "TrackCreateDate", "TrackModifyDate", "MediaCreateDate", "MediaModifyDate"):
                args.append("-QuickTime:" + tag + "=")
        result = run(exiftool, args + [
            "-api", "NoWarning=No writable tags", "-o", staged.name, str(input_path),
        ], cwd=temp)
        if result.stderr.strip():
            raise CleanError("清理时出现警告，未导出结果：" +
                             result.stderr.decode("utf-8", "replace").strip())
        sanitize_private_metadata(exiftool, staged, file_type)
        verify_profiles(exiftool, original_profiles, staged)
        after = inspect(exiftool, staged)
        if check_frames and original_pixels != decoded_snapshot(staged):
            raise CleanError("图片像素、透明度或动画发生变化，未导出结果")
        if file_type == "TIFF" and original_tiff != tiff_payload_hash(staged):
            raise CleanError("TIFF 压缩像素数据发生变化，未导出结果")
        verify(before, after, pixels_verified=check_frames, encoding_verified=file_type == "TIFF")
        if before_digest != file_digest(source):
            raise CleanError("处理期间原图被其他程序改动，未导出结果，请重试")
        return publish(staged.read_bytes(), source, suffix, anonymous=anonymous)
