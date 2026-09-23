"""Remove photo metadata locally. BMP is converted losslessly to PNG."""

import base64
import binascii
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
import xml.etree.ElementTree as ET

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


def jpeg_segments(data):
    """Yield complete JPEG markers/scans, stopping at the first image's EOI."""
    if not data.startswith(b"\xff\xd8"):
        raise CleanError("附加图层不是有效的 JPEG")
    yield 0xd8, 0, 2, b""
    pos = 2
    while pos < len(data):
        start = pos
        if data[pos] != 0xff:
            raise CleanError("JPEG 标记损坏，未导出结果")
        while pos < len(data) and data[pos] == 0xff:
            pos += 1
        if pos >= len(data):
            break
        marker = data[pos]
        pos += 1
        if marker == 0xd9:
            yield marker, start, pos, b""
            return
        if marker in {0, 0xd8}:
            raise CleanError("JPEG 图层边界无效")
        if marker == 1 or 0xd0 <= marker <= 0xd7:
            yield marker, start, pos, b""
            continue
        if pos + 2 > len(data):
            break
        length = int.from_bytes(data[pos:pos + 2], "big")
        end = pos + length
        if length < 2 or end > len(data):
            raise CleanError("JPEG 数据不完整")
        payload = data[pos + 2:end]
        if marker == 0xda:
            scan = end
            while True:
                found = data.find(b"\xff", scan)
                if found < 0:
                    raise CleanError("JPEG 图像数据不完整")
                following = found + 1
                while following < len(data) and data[following] == 0xff:
                    following += 1
                if following >= len(data):
                    raise CleanError("JPEG 图像数据不完整")
                if data[following] == 0 or 0xd0 <= data[following] <= 0xd7:
                    scan = following + 1
                    continue
                end = found
                break
        yield marker, start, end, payload
        pos = end
    raise CleanError("JPEG 缺少结束标记")


def jpeg_insert(data, segments):
    # Keep JFIF at the front where present.
    position = 2
    for marker, start, end, payload in jpeg_segments(data):
        if start == 2 and marker == 0xe0:
            position = end
        if start >= 2:
            break
    return data[:position] + segments + data[position:], position


def jpeg_render_segments(data):
    result = []
    for marker, start, end, payload in jpeg_segments(data):
        arot = marker in {0xe2, 0xea} and payload.startswith(b"AROT\0\0")
        iso = marker == 0xe2 and payload.startswith(b"urn:iso:std:iso:ts:21496:-1\0")
        ampf = (marker == 0xe0 and len(payload) == 18 and payload.startswith(b"JFIF\0")
                and payload[12:14] == b"\0\0" and payload[14:] == b"AMPF")
        if arot:
            curve_end = 10 + 4 * int.from_bytes(payload[6:10], "big")
            if (len(payload) < 10 or not curve_end <= len(payload) <= curve_end + 64
                    or any(payload[curve_end:])):
                raise CleanError("HDR 增益曲线结构无效")
        if arot or iso or ampf:
            result.append(data[start:end])
    return result


def jpeg_coding_hash(data):
    return hashlib.sha256(b"".join(data[start:end]
        for marker, start, end, _ in jpeg_segments(data)
        if not (0xe0 <= marker <= 0xef or marker == 0xfe))).digest()


def split_mpf(data):
    found = [(start, payload) for marker, start, _, payload in jpeg_segments(data)
             if marker == 0xe2 and payload.startswith(b"MPF\0")]
    if len(found) != 1:
        raise CleanError("多图 JPEG 的索引不完整")
    start, payload = found[0]
    tiff = payload[4:]
    if len(tiff) < 8 or tiff[:2] not in {b"MM", b"II"}:
        raise CleanError("多图 JPEG 索引格式无效")
    order = ">" if tiff[:2] == b"MM" else "<"
    try:
        if struct.unpack_from(order + "H", tiff, 2)[0] != 42:
            raise ValueError()
        directory = struct.unpack_from(order + "I", tiff, 4)[0]
        count = struct.unpack_from(order + "H", tiff, directory)[0]
        tags = {}
        for index in range(count):
            tag, kind, size, offset = struct.unpack_from(order + "HHII", tiff, directory + 2 + 12 * index)
            tags[tag] = kind, size, offset
        kind, size, total = tags[0xb001]
        entry_kind, entry_size, offset = tags[0xb002]
        if (kind, size) != (4, 1) or not 1 <= total <= 4090 or (entry_kind, entry_size) != (7, total * 16):
            raise ValueError()
        entries, frames, ranges = [], [], []
        for index in range(total):
            flags, length, relative, dep1, dep2 = struct.unpack_from(order + "IIIHH", tiff, offset + 16 * index)
            absolute = 0 if index == 0 else start + 8 + relative
            if (index == 0 and relative != 0) or flags & 0x07000000 or max(dep1, dep2) > total:
                raise ValueError()
            if length < 4 or absolute < 0 or absolute + length > len(data):
                raise ValueError()
            if any(absolute < finish and absolute + length > begin for begin, finish in ranges):
                raise ValueError()
            frame = data[absolute:absolute + length]
            segments = list(jpeg_segments(frame))
            # Exclude secondary indexes and bytes beyond the actual JPEG end.
            frame = b"".join(frame[a:b] for marker, a, b, body in segments
                             if not (marker == 0xe2 and body.startswith(b"MPF\0")))
            entries.append((flags, dep1, dep2))
            frames.append(frame)
            ranges.append((absolute, absolute + length))
        return entries, frames
    except (KeyError, struct.error, ValueError):
        raise CleanError("多图 JPEG 的图层位置或长度无效，未导出结果")


def join_mpf(entries, frames):
    total = len(frames)
    if total != len(entries) or not 1 <= total <= 4090:
        raise CleanError("JPEG 图层数量无效")
    # A fresh MP index contains no image identifiers or private attribute IFDs.
    prefix = (b"MPF\0MM\0*\0\0\0\x08" + struct.pack(">H", 3)
              + struct.pack(">HHI4s", 0xb000, 7, 4, b"0100")
              + struct.pack(">HHII", 0xb001, 4, 1, total)
              + struct.pack(">HHII", 0xb002, 7, 16 * total, 50) + b"\0" * 4)
    segment_size = 4 + len(prefix) + 16 * total
    _, position = jpeg_insert(frames[0], b"")
    lengths = [len(frames[0]) + segment_size] + [len(frame) for frame in frames[1:]]
    table, absolute = b"", 0
    for index, ((flags, dep1, dep2), length) in enumerate(zip(entries, lengths)):
        offset = 0 if index == 0 else absolute - position - 8
        table += struct.pack(">IIIHH", flags, length, offset, dep1, dep2)
        absolute += length
    segment = b"\xff\xe2" + struct.pack(">H", segment_size - 2) + prefix + table
    primary, _ = jpeg_insert(frames[0], segment)
    return primary + b"".join(frames[1:])


def apple_hdr_note(exiftool, source):
    raw = run(exiftool, ["-b", "-MakerNotes", str(source)]).stdout
    if not raw.startswith(b"Apple iOS\0\0\1") or raw[12:14] not in {b"MM", b"II"}:
        raise CleanError("无法读取 Apple HDR 显示参数")
    order = ">" if raw[12:14] == b"MM" else "<"
    kept = []
    try:
        count = struct.unpack_from(order + "H", raw, 14)[0]
        for index in range(count):
            tag, kind, size, offset = struct.unpack_from(order + "HHII", raw, 16 + 12 * index)
            if tag not in {0x21, 0x30}:
                continue
            if (kind, size) != (10, 1):
                raise ValueError()
            numerator, denominator = struct.unpack_from(order + "ii", raw, offset)
            if denominator == 0:
                raise ValueError()
            kept.append((tag, struct.pack(">ii", numerator, denominator)))
    except (struct.error, ValueError):
        raise CleanError("Apple HDR 显示参数损坏")
    header = b"Apple iOS\0\0\1MM" + struct.pack(">H", len(kept))
    values = b""
    for tag, value in kept:
        header += struct.pack(">HHII", tag, 10, 1, 16 + len(kept) * 12 + 4 + len(values))
        values += value
    return header + b"\0" * 4 + values


def jpeg_render_xmp(exiftool, source):
    raw = run(exiftool, ["-b", "-XMP", str(source)]).stdout.rstrip(b"\0 \t\r\n")
    if not raw:
        return b""
    if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        raise CleanError("XMP 结构不安全，未导出结果")
    namespaces = {
        "http://ns.adobe.com/hdr-gain-map/1.0/": "XMP-hdrgm",
        "http://ns.apple.com/pixeldatainfo/1.0/": "XMP-apdi",
        "http://ns.apple.com/HDRGainMap/1.0/": "XMP-HDRGainMap",
    }
    rdf = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
    root = ET.Element("{adobe:ns:meta/}xmpmeta")
    description = ET.SubElement(ET.SubElement(root, rdf + "RDF"), rdf + "Description")
    try:
        parsed = ET.fromstring(raw)
    except ET.ParseError as error:
        raise CleanError("无法读取 HDR XMP：" + str(error))
    def permitted(name, value):
        if not name.startswith("{"):
            return False
        uri, tag = name[1:].split("}", 1)
        group = namespaces.get(uri)
        return bool(group and rendering_xmp("XMP:" + group + ":" + tag, value, "JPEG"))
    for item in parsed.iter(rdf + "Description"):
        for name, value in item.attrib.items():
            if permitted(name, value):
                description.set(name, value)
        for child in item:
            if len(child) == 0 and permitted(child.tag, child.text or ""):
                ET.SubElement(description, child.tag).text = child.text
            elif len(child) == 1 and child[0].tag in {rdf + "Seq", rdf + "Bag"}:
                values = [entry.text or "" for entry in child[0]]
                if all(entry.tag == rdf + "li" and len(entry) == 0 for entry in child[0]) and permitted(child.tag, values):
                    sequence = ET.SubElement(ET.SubElement(description, child.tag), child[0].tag)
                    for value in values:
                        ET.SubElement(sequence, rdf + "li").text = value
    if not description.attrib and not len(description):
        return b""
    payload = b"http://ns.adobe.com/xap/1.0/\0" + ET.tostring(root, encoding="utf-8")
    if len(payload) > 65533:
        raise CleanError("HDR XMP 数据过大")
    return b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload


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


def clean_jpeg_frame(exiftool, source, target, before):
    original = source.read_bytes()
    preserved = jpeg_render_segments(original)
    render_xmp = jpeg_render_xmp(exiftool, source)
    args = ["-all=", "--ICC_Profile:All", "-Trailer:All=", "-TagsFromFile", "@",
            "-ColorSpaceTags", "-IFD0:Orientation"]
    if any(key.startswith("MakerNotes:") and rendering_xmp(key, value, "JPEG") for key, value in before.items()):
        note = target.with_suffix(".maker.bin")
        note.write_bytes(apple_hdr_note(exiftool, source))
        args += ["-MakerNotes<=" + note.name]
    result = run(exiftool, args + ["-api", "NoWarning=No writable tags", "-o", target.name, str(source)], cwd=target.parent)
    if result.stderr.strip():
        raise CleanError(result.stderr.decode("utf-8", "replace").strip())
    cleaned = target.read_bytes()
    retained = set(jpeg_render_segments(cleaned))
    cleaned, _ = jpeg_insert(cleaned, render_xmp + b"".join(segment for segment in preserved if segment not in retained))
    target.write_bytes(cleaned)
    sanitize_private_metadata(exiftool, target, "JPEG")
    cleaned = target.read_bytes()
    after = inspect(exiftool, target)
    verify(before, after)
    if (jpeg_coding_hash(original) != jpeg_coding_hash(cleaned)
            or Counter(preserved) != Counter(jpeg_render_segments(cleaned))):
        raise CleanError("JPEG 像素编码或 HDR 显示参数发生变化")
    verify_profiles(exiftool, profile_blocks(exiftool, source), target)


def clean_mpf(exiftool, source, staged):
    entries, frames = split_mpf(source.read_bytes())
    cleaned = []
    for index, frame in enumerate(frames):
        original = staged.parent / ("frame-" + str(index) + ".jpg")
        target = staged.parent / ("clean-frame-" + str(index) + ".jpg")
        original.write_bytes(frame)
        before = inspect(exiftool, original)
        clean_jpeg_frame(exiftool, original, target, before)
        cleaned.append(target.read_bytes())
    assembled = join_mpf(entries, cleaned)
    final_entries, final_frames = split_mpf(assembled)
    if final_entries != entries or final_frames != cleaned:
        raise CleanError("多图 JPEG 索引校验失败")
    staged.write_bytes(assembled)


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
    multi_jpeg = file_type == "JPEG" and any(key.startswith("MPF:") for key in before)
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
        if multi_jpeg:
            clean_mpf(exiftool, source, staged)
        elif file_type == "JPEG":
            if input_path == source and source.suffix.lower() not in suffixes:
                input_path = Path(temp) / "source.jpg"
                shutil.copyfile(source, input_path)
            clean_jpeg_frame(exiftool, input_path, staged, before)
        else:
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
        if file_type != "JPEG":
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
