"""JPEG: rebuild each image from the segments needed to decode and show it.

Copied unchanged: quantization and Huffman tables, frame and scan headers with
their compressed data, restart intervals, Adobe color-transform information,
and HDR data (ISO 21496-1 gain-map metadata, Apple gain curves and Apple's MPF
marker). Written afresh: JFIF (density only, no thumbnail), EXIF (orientation,
resolution, color space, Apple HDR headroom), XMP (recognized HDR fields), the
ICC profile (sanitized) and the multi-picture (MPF) index, which is how HDR
gain maps are attached. Everything else is dropped, including comments,
IPTC/Photoshop blocks, C2PA, thumbnails, maker notes and trailing data.
"""
import hashlib
import struct

from .. import exif, xmp
from ..errors import FormatError, VerificationError
from ..privacy import PrivacyError, sanitize_icc

SOI, EOI, SOS, APP0, APP1, APP2, APP10, APP14, COM = 0xD8, 0xD9, 0xDA, 0xE0, 0xE1, 0xE2, 0xEA, 0xEE, 0xFE
# Frame headers (SOF0-SOF15 except the table markers), tables, restart interval,
# number of lines, temporary marker. RST markers are part of the scan data.
CODING = ({0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
          | {0xC4, 0xCC, 0xDB, 0xDD, 0xDC, 0x01, SOS})
EXIF_ID, XMP_ID = b"Exif\0\0", b"http://ns.adobe.com/xap/1.0/\0"
ICC_ID, MPF_ID = b"ICC_PROFILE\0", b"MPF\0"
ISO_GAIN_MAP_ID, APPLE_CURVE_ID = b"urn:iso:std:iso:ts:21496:-1\0", b"AROT\0\0"


def rebuild(data):
    entries, frames = images(data)
    cleaned = [b"".join(expected_segments(frame)) for frame in frames]
    return join_mpf(entries, cleaned) if entries else cleaned[0]


def verify(original, rebuilt):
    """Parse the result on its own: a fresh MPF index linking exactly the
    original's images, each holding exactly the segments it should."""
    source_entries, source_frames = images(original)
    entries, frames = images(rebuilt, strict=True)
    if entries != source_entries or len(frames) != len(source_frames):
        raise VerificationError("verification_failed", detail="JPEG image list differs")
    for source, frame in zip(source_frames, frames):
        found = [frame[start:end] for marker, start, end, payload in segments(frame)
                 if not (marker == APP2 and payload.startswith(MPF_ID))]
        if found != expected_segments(source):
            raise VerificationError("verification_failed", detail="JPEG segments differ from the original")


def expected_segments(data):
    """The segments a clean copy of one image holds, in the original's order."""
    parsed = list(segments(data))
    exif_segment = rewritten_exif(parsed)
    xmp_segment = rewritten_xmp(parsed)
    profile_slices = iter(sanitized_profile_slices(parsed))
    result, seen_jfif, seen_exif, seen_xmp = [], False, False, False
    for marker, start, end, payload in parsed:
        if marker in (SOI, EOI) or marker in CODING:
            result.append(data[start:end])
        elif marker == APP0 and payload.startswith(b"JFIF\0") and not seen_jfif:
            seen_jfif = True
            if len(payload) < 14:
                raise damaged()
            # Apple's gain-map marker is an exact 18-byte JFIF variant; keep it as it is.
            result.append(data[start:end] if is_apple_mpf_marker(payload)
                          else segment(APP0, payload[:12] + b"\0\0"))
        elif marker == APP1 and payload.startswith(EXIF_ID):
            if not seen_exif and exif_segment:
                result.append(exif_segment)
            seen_exif = True
        elif marker == APP1 and payload.startswith(XMP_ID):
            if not seen_xmp and xmp_segment:
                result.append(xmp_segment)
            seen_xmp = True
        elif marker == APP2 and payload.startswith(ICC_ID):
            result.append(segment(APP2, payload[:14] + next(profile_slices)))
        elif is_rendering_segment(marker, payload):
            result.append(data[start:end])
        elif 0xE0 <= marker <= 0xEF or marker == COM:
            continue  # other application data and comments
        else:
            raise FormatError("unsupported_part", format="JPEG", part="marker %02X" % marker)
    return result


def rewritten_exif(parsed):
    payloads = [payload for marker, _, _, payload in parsed if marker == APP1 and payload.startswith(EXIF_ID)]
    block = exif.build(exif.display_fields(payloads[0])) if payloads else b""
    return segment(APP1, EXIF_ID + block) if block else None


def rewritten_xmp(parsed):
    packets = [payload[len(XMP_ID):] for marker, _, _, payload in parsed
               if marker == APP1 and payload.startswith(XMP_ID)]
    try:
        packet = xmp.hdr_packet(xmp.hdr_fields(packets[0])) if packets else b""
    except xmp.XMPError as error:
        raise FormatError("unsupported_part", format="JPEG", part="XMP: " + str(error))
    if len(XMP_ID) + len(packet) > 65533:
        raise FormatError("unsupported_part", format="JPEG", part="oversized HDR XMP")
    return segment(APP1, XMP_ID + packet) if packet else None


def sanitized_profile_slices(parsed):
    """An ICC profile may span several APP2 segments; sanitize it as a whole and
    return new contents for each segment, of the same sizes."""
    pieces, total = {}, None
    for marker, _, _, payload in parsed:
        if marker == APP2 and payload.startswith(ICC_ID):
            if len(payload) < 14:
                raise damaged()
            index, declared = payload[12], payload[13]
            if not 1 <= index <= declared or index in pieces or total not in (None, declared):
                raise damaged()
            total = declared
            pieces[index] = payload[14:]
    if not pieces:
        return []
    if set(pieces) != set(range(1, total + 1)):
        raise damaged()
    try:
        clean = sanitize_icc(b"".join(pieces[index] for index in sorted(pieces)))
    except PrivacyError as error:
        raise FormatError("unsupported_profile", format="JPEG", detail=str(error))
    slices, position = {}, 0
    for index in sorted(pieces):
        slices[index] = clean[position:position + len(pieces[index])]
        position += len(pieces[index])
    # Segments are emitted in file order, which need not be the sequence order.
    order = [payload[12] for marker, _, _, payload in parsed if marker == APP2 and payload.startswith(ICC_ID)]
    return [slices[index] for index in order]


def is_rendering_segment(marker, payload):
    """HDR data kept verbatim: ISO 21496-1 gain-map metadata and Apple's gain curve."""
    if marker == APP2 and payload.startswith(ISO_GAIN_MAP_ID):
        return True
    if marker in (APP2, APP10) and payload.startswith(APPLE_CURVE_ID):
        if len(payload) < 10:
            raise damaged()
        curve_end = 10 + 4 * int.from_bytes(payload[6:10], "big")
        if not curve_end <= len(payload) <= curve_end + 64 or any(payload[curve_end:]):
            raise FormatError("unsupported_part", format="JPEG", part="HDR gain curve layout")
        return True
    # Adobe's segment tells decoders how CMYK/YCCK data is stored; exactly 12 bytes.
    return marker == APP14 and payload.startswith(b"Adobe") and len(payload) == 12


def is_apple_mpf_marker(payload):
    return len(payload) == 18 and payload[12:14] == b"\0\0" and payload[14:] == b"AMPF"


def segment(marker, payload):
    if len(payload) > 65533:
        raise damaged()
    return bytes((0xFF, marker)) + struct.pack(">H", len(payload) + 2) + payload


def coding_hash(data):
    """Hash of everything that decodes the pixels: tables, headers and scans."""
    return hashlib.sha256(b"".join(data[start:end] for marker, start, end, _ in segments(data)
                                   if not (0xE0 <= marker <= 0xEF or marker == COM))).digest()


def segments(data):
    """(marker, start, end, payload) for one image, up to its EOI. A scan's range
    includes its compressed data; `start` includes any fill bytes."""
    if not data.startswith(b"\xff\xd8"):
        raise damaged()
    yield SOI, 0, 2, b""
    position = 2
    while position < len(data):
        start = position
        if data[position] != 0xFF:
            raise damaged()
        while position < len(data) and data[position] == 0xFF:
            position += 1
        if position >= len(data):
            break
        marker = data[position]
        position += 1
        if marker == EOI:
            yield marker, start, position, b""
            return
        if marker in (0, SOI):
            raise damaged()
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            yield marker, start, position, b""
            continue
        if position + 2 > len(data):
            break
        length = int.from_bytes(data[position:position + 2], "big")
        end = position + length
        if length < 2 or end > len(data):
            raise damaged()
        payload = data[position + 2:end]
        if marker == SOS:
            end = scan_end(data, end)
        yield marker, start, end, payload
        position = end
    raise damaged()


def scan_end(data, position):
    """Where compressed scan data ends: the first FF that is neither stuffing
    (FF 00) nor a restart marker (FF D0-D7)."""
    while True:
        found = data.find(b"\xff", position)
        if found < 0:
            raise damaged()
        following = found + 1
        while following < len(data) and data[following] == 0xFF:
            following += 1
        if following >= len(data):
            raise damaged()
        if data[following] == 0 or 0xD0 <= data[following] <= 0xD7:
            position = following + 1
            continue
        return found


def images(data, strict=False):
    """([(flags, dependent1, dependent2)], [image bytes]) of an MPF file, or
    ([], [first image]) without an MPF index. With strict, the index may hold
    nothing but its structure and the images must fill the file exactly."""
    indexes = [(start, end, payload) for marker, start, end, payload in segments(data)
               if marker == APP2 and payload.startswith(MPF_ID)]
    if not indexes:
        end = next(end for marker, _, end, _ in segments(data) if marker == EOI)
        if strict and end != len(data):
            raise VerificationError("verification_failed", detail="data after the JPEG end")
        return [], [data[:end]]
    if len(indexes) != 1:
        raise damaged()
    start, end, payload = indexes[0]
    try:
        entries, frames, ranges, bare_index = split_mpf(data, start, payload[4:])
    except (KeyError, IndexError, struct.error, ValueError):
        raise damaged()
    if strict:
        if not bare_index:
            raise VerificationError("verification_failed", detail="MPF index holds more than the image list")
        # Each image fills exactly its range (the first one plus its index), and
        # the ranges fill the file, so nothing can sit between or after them.
        sizes = [len(frames[0]) + end - start] + [len(frame) for frame in frames[1:]]
        if (sorted(ranges) != ranges or ranges[0][0] != 0 or ranges[-1][1] != len(data)
                or any(a[1] != b[0] for a, b in zip(ranges, ranges[1:]))
                or [b - a for a, b in ranges] != sizes
                or any(frame != data[a:b] for frame, (a, b) in zip(frames[1:], ranges[1:]))):
            raise VerificationError("verification_failed", detail="JPEG images do not fill the file")
    return entries, frames


def split_mpf(data, start, tiff):
    """Entries, images and byte ranges listed by the MPF index in the segment at
    `start`, and whether the index holds nothing but the image list."""
    order = {b"MM": ">", b"II": "<"}[tiff[:2]]
    if struct.unpack_from(order + "H", tiff, 2)[0] != 42:
        raise ValueError("MPF header")
    directory = struct.unpack_from(order + "I", tiff, 4)[0]
    count = struct.unpack_from(order + "H", tiff, directory)[0]
    tags = {}
    for index in range(count):
        tag, kind, size, offset = struct.unpack_from(order + "HHII", tiff, directory + 2 + 12 * index)
        tags[tag] = kind, size, offset
    next_directory = struct.unpack_from(order + "I", tiff, directory + 2 + 12 * count)[0]
    bare_index = set(tags) == {0xB000, 0xB001, 0xB002} and not next_directory
    kind, size, total = tags[0xB001]
    entry_kind, entry_size, offset = tags[0xB002]
    if (kind, size) != (4, 1) or not 1 <= total <= 4090 or (entry_kind, entry_size) != (7, total * 16):
        raise ValueError("MPF entries")
    entries, frames, ranges = [], [], []
    for index in range(total):
        flags, length, relative, dependent1, dependent2 = struct.unpack_from(
            order + "IIIHH", tiff, offset + 16 * index)
        absolute = 0 if index == 0 else start + 8 + relative
        if (index == 0 and relative != 0) or flags & 0x07000000 or max(dependent1, dependent2) > total:
            raise ValueError("MPF entry")
        if length < 4 or absolute + length > len(data):
            raise ValueError("MPF range")
        if any(absolute < finish and absolute + length > begin for begin, finish in ranges):
            raise ValueError("overlapping MPF images")
        frame = data[absolute:absolute + length]
        # Leave out secondary indexes and anything past each image's own end.
        frames.append(b"".join(frame[a:b] for marker, a, b, body in segments(frame)
                               if not (marker == APP2 and body.startswith(MPF_ID))))
        entries.append((flags, dependent1, dependent2))
        ranges.append((absolute, absolute + length))
    return entries, frames, ranges, bare_index


def join_mpf(entries, frames):
    """A fresh MPF file: the first image carries an index holding only the
    version, image count and image list (no image IDs or attributes)."""
    total = len(frames)
    if total != len(entries) or not 1 <= total <= 4090:
        raise damaged()
    prefix = (MPF_ID + b"MM\0*\0\0\0\x08" + struct.pack(">H", 3)
              + struct.pack(">HHI4s", 0xB000, 7, 4, b"0100")
              + struct.pack(">HHII", 0xB001, 4, 1, total)
              + struct.pack(">HHII", 0xB002, 7, 16 * total, 50) + b"\0" * 4)
    size = 4 + len(prefix) + 16 * total
    position = index_position(frames[0])
    lengths = [len(frames[0]) + size] + [len(frame) for frame in frames[1:]]
    table, absolute = b"", 0
    for index, ((flags, dependent1, dependent2), length) in enumerate(zip(entries, lengths)):
        offset = 0 if index == 0 else absolute - position - 8
        table += struct.pack(">IIIHH", flags, length, offset, dependent1, dependent2)
        absolute += length
    index_segment = b"\xff\xe2" + struct.pack(">H", size - 2) + prefix + table
    first = frames[0][:position] + index_segment + frames[0][position:]
    return first + b"".join(frames[1:])


def index_position(frame):
    """The MPF index goes right after SOI, or after a leading JFIF segment."""
    parsed = list(segments(frame))
    if len(parsed) > 1 and parsed[1][0] == APP0 and parsed[1][1] == 2:
        return parsed[1][2]
    return 2


def damaged():
    return FormatError("damaged", format="JPEG")
