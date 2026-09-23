"""TIFF: write a new file from the tags and image data needed to show each page.

Every page (IFD in the main chain) keeps its image data (strips or tiles,
copied unchanged, with shared JPEG tables) and the tags that describe how to
decode and show it: dimensions, sample layout, compression, color
interpretation and palette, resolution, orientation, page number and a
sanitized ICC profile. Everything else is dropped: EXIF and GPS directories,
XMP, IPTC and Photoshop blocks, descriptive text, private tags, sub-images such
as thumbnails, and free space. The byte order is kept, since 16-bit samples are
stored in it. BigTIFF and old-style JPEG compression are refused, and so is
metadata inside JPEG-compressed strips. So are RAW photos built on TIFF (DNG,
CR2, NEF...): their first page is only a preview.

Nothing kept may carry extra bytes: every kept tag holds exactly the number of
values the specification gives it, a page holds exactly the strips or tiles
its image needs, and uncompressed ones have exactly their size.
"""
import struct
from math import ceil

from .. import icc
from ..errors import FormatError, VerificationError
from ..exif import LONG, SHORT, TYPE_SIZES, UNDEFINED
from . import jpeg

CLASSIC, BIG = 42, 43  # the version after the byte order; 43 is BigTIFF
NEW_SUBFILE_TYPE, WIDTH, HEIGHT, BITS, COMPRESSION, PHOTOMETRIC = 254, 256, 257, 258, 259, 262
SAMPLES, ROWS_PER_STRIP, PLANAR, TILE_WIDTH, TILE_LENGTH = 277, 278, 284, 322, 323
STRIPS, STRIP_COUNTS, TILES, TILE_COUNTS = 273, 279, 324, 325
UNCOMPRESSED, OLD_JPEG, JPEG_COMPRESSION, YCBCR = 1, 6, 7, 6
JPEG_TABLES, ICC = 347, 34675
SUB_IFDS, DNG_VERSION = 330, 50706
MAX_PAGES = 10000
# The number of values of kept tags: fixed, or one per sample (one value
# alone also stands for every sample).
COUNTS = {254: 1, 255: 1, 256: 1, 257: 1, 259: 1, 262: 1, 263: 1, 266: 1, 274: 1, 277: 1, 278: 1,
          282: 1, 283: 1, 284: 1, 290: 1, 292: 1, 293: 1, 296: 1, 297: 2, 317: 1, 318: 2, 319: 6,
          321: 2, 322: 1, 323: 1, 332: 1, 334: 1, 342: 6, 529: 3, 530: 2, 531: 1, 532: 6}
PER_SAMPLE = {258, 280, 281, 339, 340, 341}
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
    JPEG_TABLES,
    529, 530, 531, 532,            # YCbCr coefficients, subsampling, positioning, reference black/white
    ICC,
}


def rebuild(data):
    if data[8:10] == b"CR":  # Canon CR2 marks itself right after the TIFF header
        raise FormatError("raw_photo")
    order, pages = parse(data)
    output = bytearray((b"II*\0" if order == "<" else b"MM\0*") + b"\0" * 4)
    link = 4  # where the offset of the next page's directory goes
    for page in pages:
        entries = {tag: value for tag, value in page["tags"].items() if tag in KEPT}
        if ICC in entries:
            try:
                entries[ICC] = (UNDEFINED, icc.sanitize(entries[ICC][1]))
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
            expected[ICC] = (UNDEFINED, icc.sanitize(expected[ICC][1]))
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
    if magic == BIG:
        raise FormatError("unsupported_variant", format="BigTIFF")
    if magic != CLASSIC:
        raise damaged()
    pages, seen, offset = [], set(), first
    while offset:
        if offset in seen or len(pages) > MAX_PAGES:
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
    # A DNG, or a preview page whose full image sits in a sub-IFD, is a RAW photo.
    if DNG_VERSION in tags or (SUB_IFDS in tags and value(tags, NEW_SUBFILE_TYPE, 0, order) & 1):
        raise FormatError("raw_photo")
    if value(tags, COMPRESSION, UNCOMPRESSED, order) == OLD_JPEG:
        raise FormatError("unsupported_variant", format="TIFF (old-style JPEG)")
    check_counts(tags, order)
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
    check_layout(tags, blocks, order)
    if value(tags, COMPRESSION, UNCOMPRESSED, order) == JPEG_COMPRESSION:
        for block in blocks + ([tags[JPEG_TABLES][1]] if JPEG_TABLES in tags else []):
            check_jpeg_block(block)
    next_offset = unpack(data, order + "I", table_end - 4)[0]
    return {"tags": tags, "blocks": blocks, "spans": spans}, next_offset


def check_counts(tags, order):
    """Kept tags hold exactly as many values as the specification gives them."""
    samples = value(tags, SAMPLES, 1, order)
    bits = max(integers(tags[BITS], order)) if BITS in tags else 1
    for tag, (kind, data) in tags.items():
        count = len(data) // TYPE_SIZES[kind]
        allowed = ({COUNTS[tag]} if tag in COUNTS else {1, samples} if tag in PER_SAMPLE
                   else set(range(samples + 1)) if tag == 338       # extra samples
                   else {2, 2 * samples} if tag == 336                # dot range
                   else {1 << bits} if tag == 291                     # gray response curve
                   else {1 << bits, 3 << bits} if tag == 301          # transfer function
                   else {3 << bits} if tag == 320 else None)          # palette
        if tag in KEPT and allowed is not None and count not in allowed:
            raise damaged()


def check_layout(tags, blocks, order):
    """A page holds exactly the strips or tiles its image needs; uncompressed
    ones hold exactly their pixels."""
    width, height = value(tags, WIDTH, 0, order), value(tags, HEIGHT, 0, order)
    samples = value(tags, SAMPLES, 1, order)
    bits = integers(tags[BITS], order) if BITS in tags else [1]
    if not width or not height or len(bits) not in (1, samples):
        raise damaged()
    bits = bits * samples if len(bits) == 1 else bits
    # Bits per pixel of each plane: samples are stored together, or one plane each.
    planes = [sum(bits)] if value(tags, PLANAR, 1, order) == 1 else bits
    if TILES in tags:
        tile_width, tile_length = value(tags, TILE_WIDTH, 0, order), value(tags, TILE_LENGTH, 0, order)
        if not tile_width or not tile_length:
            raise damaged()
        tiles = ceil(width / tile_width) * ceil(height / tile_length)
        sizes = [tile_length * ((tile_width * plane + 7) // 8) for plane in planes for _ in range(tiles)]
    else:
        rows = min(value(tags, ROWS_PER_STRIP, height, order) or height, height)
        strips = ceil(height / rows)
        sizes = [min(rows, height - n * rows) * ((width * plane + 7) // 8)
                 for plane in planes for n in range(strips)]
    if len(blocks) != len(sizes):
        raise damaged()
    # Subsampled YCbCr packs its samples differently; compressed sizes are unknown.
    if value(tags, COMPRESSION, UNCOMPRESSED, order) == UNCOMPRESSED and value(tags, PHOTOMETRIC, 0, order) != YCBCR:
        if any(len(block) > size for block, size in zip(blocks, sizes)):
            raise FormatError("extra_image_data", format="TIFF")
        if any(len(block) < size for block, size in zip(blocks, sizes)):
            raise damaged()


def check_jpeg_block(block):
    """JPEG-compressed strips and tables may hold only decoding segments."""
    if not block:
        return
    for marker, _, _, payload in jpeg.segments(block + (b"" if block.endswith(b"\xff\xd9") else b"\xff\xd9")):
        adobe = marker == jpeg.APP14 and payload.startswith(b"Adobe") and len(payload) == 12
        if (marker in jpeg.APPLICATION or marker == jpeg.COM) and not adobe:
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


def value(tags, tag, default, order):
    """A tag's first value, or `default` without the tag."""
    return integers(tags[tag], order)[0] if tag in tags else default


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
