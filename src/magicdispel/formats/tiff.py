"""TIFF: write a new file from the tags and image data needed to show each page.

Every page (IFD in the main chain) keeps its image data (strips or tiles,
copied unchanged, with shared JPEG tables) and the tags that describe how to
decode and show it: dimensions, sample layout, compression, color
interpretation and palette, resolution, orientation, page number and a
sanitized ICC profile. Everything else is dropped: EXIF and GPS directories,
XMP, IPTC and Photoshop blocks, descriptive text, private tags, sub-images such
as thumbnails, and free space. The byte order is kept, since 16-bit samples are
stored in it. BigTIFF and old-style JPEG compression are refused, and so is
metadata inside JPEG-compressed strips.
"""
import struct

from ..errors import FormatError, VerificationError
from .. import icc
from . import jpeg

TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4}
SHORT, LONG = 3, 4
STRIPS, STRIP_COUNTS, TILES, TILE_COUNTS = 273, 279, 324, 325
COMPRESSION, JPEG_COMPRESSION, OLD_JPEG = 259, 7, 6
ICC = 34675
# Tags needed to decode and show a page, copied unchanged (offsets are recomputed).
KEPT = {
    254, 255,                      # new and old subfile type
    256, 257, 258, 259, 262, 263,  # size, bits per sample, compression, photometric, thresholding
    266, 273, 274, 277, 278, 279,  # fill order, strip offsets, orientation, samples, rows per strip, counts
    280, 281, 282, 283, 284,       # min/max sample value, x/y resolution, planar configuration
    290, 291, 292, 293, 296, 297,  # gray response, T4/T6 options, resolution unit, page number
    301, 317, 318, 319, 320, 321,  # transfer function, predictor, white point, primaries, palette, halftone
    322, 323, 324, 325,            # tile size, offsets and counts
    332, 334, 336, 338, 339, 340, 341, 342,  # inks, dot range, extra samples, sample format and range
    347,                           # JPEG tables
    529, 530, 531, 532,            # YCbCr coefficients, subsampling, positioning, reference black/white
    ICC,
}


def rebuild(data):
    order, pages = parse(data)
    output = bytearray((b"II*\0" if order == "<" else b"MM\0*") + b"\0" * 4)
    link = 4  # where the offset of the next page's directory goes
    for page in pages:
        entries = {tag: value for tag, value in page["tags"].items() if tag in KEPT}
        if ICC in entries:
            try:
                entries[ICC] = (7, icc.sanitize(entries[ICC][1]))
            except icc.ProfileError as error:
                raise FormatError("unsupported_profile", format="TIFF", detail=str(error))
        offsets = []
        for block in page["blocks"]:
            align(output)
            offsets.append(len(output) if block else 0)
            output += block
        tag = TILES if TILES in page["tags"] else STRIPS
        entries[tag] = (LONG, struct.pack(order + "%dI" % len(offsets), *offsets))
        align(output)
        struct.pack_into(order + "I", output, link, len(output))
        link = write_directory(output, entries, order)
    return bytes(output)


def verify(original, rebuilt):
    """Parse the result on its own: the same pages with only kept tags, equal
    values and image data, a sanitized profile, and no byte unaccounted for."""
    source_order, source_pages = parse(original)
    order, pages = parse(rebuilt)
    if order != source_order or len(pages) != len(source_pages):
        fail("TIFF pages differ")
    for source, page in zip(source_pages, pages):
        if page["blocks"] != source["blocks"]:
            fail("TIFF image data differs")
        expected = {tag: value for tag, value in source["tags"].items() if tag in KEPT and tag not in (STRIPS, TILES)}
        if ICC in expected:
            expected[ICC] = (7, icc.sanitize(expected[ICC][1]))
        found = {tag: value for tag, value in page["tags"].items() if tag not in (STRIPS, TILES)}
        if found != expected:
            fail("TIFF tags differ from the original")
    covered = sorted(span for page in pages for span in page["spans"]) + [(0, 8)]
    position = 0
    for start, end in sorted(covered):
        if start > position and any(rebuilt[position:start]):
            fail("unaccounted TIFF bytes")
        position = max(position, end)
    # A final value of odd length is followed by one zero byte of padding.
    if len(rebuilt) - position > 1 or any(rebuilt[position:]):
        fail("data after the TIFF end")


def parse(data):
    """The byte order and, for each page in the main chain, its tags
    {tag: (type, value bytes)}, image blocks and the byte spans it uses."""
    order = {b"II": "<", b"MM": ">"}.get(data[:2])
    if order is None or len(data) < 8:
        raise damaged()
    magic, first = struct.unpack_from(order + "HI", data, 2)
    if magic == 43:
        raise FormatError("unsupported_variant", format="BigTIFF")
    if magic != 42:
        raise damaged()
    pages, seen, offset = [], set(), first
    while offset:
        if offset in seen or len(pages) > 10000:
            raise damaged()
        seen.add(offset)
        page, offset = read_directory(data, offset, order)
        pages.append(page)
    if not pages:
        raise damaged()
    return order, pages


def read_directory(data, offset, order):
    count = unpack(data, order + "H", offset)[0]
    table_end = offset + 2 + 12 * count + 4
    if table_end > len(data):
        raise damaged()
    tags, spans = {}, [(offset, table_end)]
    for index in range(count):
        tag, kind, number = unpack(data, order + "HHI", offset + 2 + 12 * index)
        size = TYPE_SIZES.get(kind, 0) * number
        if not size:
            continue  # an unknown type or empty value cannot be meaningful display data
        field = offset + 2 + 12 * index + 8
        start = field if size <= 4 else unpack(data, order + "I", field)[0]
        if start + size > len(data):
            raise damaged()
        tags[tag] = (kind, data[start:start + size])
        if size > 4:
            spans.append((start, start + size))
    if tags.get(COMPRESSION) and integers(tags[COMPRESSION], order) == [OLD_JPEG]:
        raise FormatError("unsupported_variant", format="TIFF (old-style JPEG)")
    offsets_tag, counts_tag = (TILES, TILE_COUNTS) if TILES in tags else (STRIPS, STRIP_COUNTS)
    if offsets_tag not in tags or counts_tag not in tags:
        raise damaged()
    offsets, counts = integers(tags[offsets_tag], order), integers(tags[counts_tag], order)
    if len(offsets) != len(counts):
        raise damaged()
    blocks = []
    for start, size in zip(offsets, counts):
        if start + size > len(data) or (size and start < 8):
            raise damaged()
        blocks.append(data[start:start + size])
        if size:
            spans.append((start, start + size))
    if tags.get(COMPRESSION) and integers(tags[COMPRESSION], order) == [JPEG_COMPRESSION]:
        for block in blocks + ([tags[347][1]] if 347 in tags else []):
            check_jpeg_block(block)
    next_offset = unpack(data, order + "I", table_end - 4)[0]
    return {"tags": tags, "blocks": blocks, "spans": spans}, next_offset


def check_jpeg_block(block):
    """JPEG-compressed strips and tables may hold only decoding segments."""
    if not block:
        return
    for marker, _, _, payload in jpeg.segments(block + (b"" if block.endswith(b"\xff\xd9") else b"\xff\xd9")):
        adobe = marker == jpeg.APP14 and payload.startswith(b"Adobe") and len(payload) == 12
        if (0xE0 <= marker <= 0xEF or marker == jpeg.COM) and not adobe:
            raise FormatError("unsupported_part", format="TIFF", part="metadata inside JPEG strips")


def write_directory(output, entries, order):
    """Append an IFD and its out-of-line values; return where its next-IFD link is."""
    start = len(output)
    table_size = 2 + 12 * len(entries) + 4
    values_at = start + table_size
    table, values = struct.pack(order + "H", len(entries)), b""
    for tag in sorted(entries):
        kind, value = entries[tag]
        count = len(value) // TYPE_SIZES[kind]
        if len(value) <= 4:
            table += struct.pack(order + "HHI", tag, kind, count) + value.ljust(4, b"\0")
        else:
            table += struct.pack(order + "HHII", tag, kind, count, values_at + len(values))
            values += value + b"\0" * (len(value) % 2)
    output += table + b"\0" * 4 + values
    return start + table_size - 4


def integers(entry, order):
    kind, value = entry
    if kind not in (SHORT, LONG):
        raise damaged()
    return list(struct.unpack(order + ("%dH" if kind == SHORT else "%dI") % (len(value) // TYPE_SIZES[kind]), value))


def unpack(data, fmt, offset):
    if offset < 0 or offset + struct.calcsize(fmt) > len(data):
        raise damaged()
    return struct.unpack_from(fmt, data, offset)


def align(output):
    if len(output) % 2:
        output.append(0)


def fail(detail):
    raise VerificationError("verification_failed", detail=detail)


def damaged():
    return FormatError("damaged", format="TIFF")
