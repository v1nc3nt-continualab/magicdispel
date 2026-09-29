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
values the specification gives it, of a type it may have, a page holds exactly
the strips or tiles its image needs, and uncompressed ones have exactly their
size. JPEG tables are kept for JPEG compression alone, and a palette for
palette images.
"""
import struct
from math import ceil

from .. import icc
from ..errors import FormatError, VerificationError
from ..exif import BYTE, LONG, RATIONAL, SHORT, TYPE_SIZES, UNDEFINED
from . import jpeg

CLASSIC, BIG = 42, 43  # the version after the byte order; 43 is BigTIFF
NEW_SUBFILE_TYPE, WIDTH, HEIGHT, BITS, COMPRESSION, PHOTOMETRIC = 254, 256, 257, 258, 259, 262
SAMPLES, ROWS_PER_STRIP, PLANAR, TILE_WIDTH, TILE_LENGTH = 277, 278, 284, 322, 323
STRIPS, STRIP_COUNTS, TILES, TILE_COUNTS = 273, 279, 324, 325
UNCOMPRESSED, OLD_JPEG, JPEG_COMPRESSION, YCBCR, PALETTE = 1, 6, 7, 6, 3
JPEG_TABLES, ICC, COLOR_MAP = 347, 34675, 320
SUB_IFDS, DNG_VERSION = 330, 50706
MAX_PAGES = 10000
MAX_SAMPLES = 4096  # samples per pixel: a LONG could say 4 billion, and lists of that many values would follow
MAX_BITS = 64       # bits per sample: 1 << bits is computed for the response curve and palette sizes
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
# The types the values of kept tags may have: whole numbers, fractions, any number, or bytes. Another
# type would only be a way to hold more bytes than the values need.
WHOLE, NUMBERS, BYTES = {BYTE, SHORT, LONG}, set(TYPE_SIZES) - {2, UNDEFINED, 13}, {BYTE, UNDEFINED}
TYPES = {tag: WHOLE for tag in KEPT}
TYPES.update({tag: {RATIONAL} for tag in (282, 283, 318, 319, 529)})
TYPES.update({532: WHOLE | {RATIONAL}, 340: NUMBERS, 341: NUMBERS, JPEG_TABLES: BYTES, ICC: BYTES})


def kept_tags(tags, order):
    """The tags of a page that a clean copy holds: those it is decoded by. JPEG
    tables serve JPEG compression alone, and a palette a palette image."""
    compression, photometric = value(tags, COMPRESSION, UNCOMPRESSED, order), value(tags, PHOTOMETRIC, 0, order)
    return {tag: entry for tag, entry in tags.items() if tag in KEPT
            and (tag != JPEG_TABLES or compression == JPEG_COMPRESSION)
            and (tag != COLOR_MAP or photometric == PALETTE)}


def rebuild(data):
    if data[8:10] == b"CR":  # Canon CR2 marks itself right after the TIFF header
        raise FormatError("raw_photo")
    order, pages = parse(data)
    output = bytearray((b"II*\0" if order == "<" else b"MM\0*") + b"\0" * 4)
    link = 4  # where the offset of the next page's directory goes
    for page in pages:
        entries = kept_tags(page["tags"], order)
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
        expected = {tag: value for tag, value in kept_tags(source["tags"], order).items() if tag not in (STRIPS, TILES)}
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
    pages, seen, offset, claims = [], set(), first, Claims(len(data))
    while offset:
        if offset in seen or len(pages) > MAX_PAGES:
            raise damaged()
        seen.add(offset)
        page, offset = read_directory(data, offset, order, claims)
        pages.append(page)
    if not pages:
        raise damaged()
    # Image blocks share no byte: what would come out of one file is what it holds.
    used = sorted(span for page in pages for span in page["image"])
    if any(later[0] < earlier[1] for earlier, later in zip(used, used[1:])):
        raise damaged()
    return order, pages


class Claims:
    """The bytes a file's tags and image blocks name. A range is copied once
    however many name it, and the ranges together hold no more than the file
    does, as those of a real file, which share nothing, do not: thousands of
    entries naming one large range would otherwise need thousands of copies."""

    def __init__(self, size):
        self.left, self.copies = size, {}

    def take(self, data, start, size):
        if (start, size) not in self.copies:
            self.left -= size
            if self.left < 0:
                raise damaged()
            self.copies[start, size] = data[start:start + size]
        return self.copies[start, size]


def read_directory(data, offset, order, claims):
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
        tags[tag] = (kind, claims.take(data, start, size))
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
    blocks, image = [], []
    for start, size in zip(offsets, counts):
        if start + size > len(data) or (size and start < 8):
            raise damaged()
        blocks.append(claims.take(data, start, size))
        if size:
            spans.append((start, start + size))
            image.append((start, start + size))
    check_layout(tags, blocks, order)
    if value(tags, COMPRESSION, UNCOMPRESSED, order) == JPEG_COMPRESSION:
        for block in blocks + ([tags[JPEG_TABLES][1]] if JPEG_TABLES in tags else []):
            check_jpeg_block(block)
    next_offset = unpack(data, order + "I", table_end - 4)[0]
    return {"tags": tags, "blocks": blocks, "spans": spans, "image": image}, next_offset


def check_counts(tags, order):
    """Kept tags hold exactly as many values as the specification gives them."""
    samples = value(tags, SAMPLES, 1, order)
    if samples > MAX_SAMPLES:
        raise damaged()
    bits = max(integers(tags[BITS], order)) if BITS in tags else 1
    if bits > MAX_BITS:
        raise damaged()
    for tag, (kind, data) in tags.items():
        count = len(data) // TYPE_SIZES[kind]
        allowed = ({COUNTS[tag]} if tag in COUNTS else {1, samples} if tag in PER_SAMPLE
                   else range(samples + 1) if tag == 338              # extra samples
                   else {2, 2 * samples} if tag == 336                # dot range
                   else {1 << bits} if tag == 291                     # gray response curve
                   else {1 << bits, 3 << bits} if tag == 301          # transfer function
                   else {3 << bits} if tag == COLOR_MAP else None)    # palette
        if tag in KEPT and (kind not in TYPES[tag] or allowed is not None and count not in allowed):
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
        if len(blocks) != tiles * len(planes):  # before any list of that many sizes: it may be billions
            raise damaged()
        sizes = [tile_length * ((tile_width * plane + 7) // 8) for plane in planes for _ in range(tiles)]
    else:
        rows = min(value(tags, ROWS_PER_STRIP, height, order) or height, height)
        strips = ceil(height / rows)
        if len(blocks) != strips * len(planes):
            raise damaged()
        sizes = [min(rows, height - n * rows) * ((width * plane + 7) // 8)
                 for plane in planes for n in range(strips)]
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
    if kind not in WHOLE:
        raise damaged()
    code = {BYTE: "B", SHORT: "H", LONG: "I"}[kind]
    return list(struct.unpack(order + "%d" % (len(value) // TYPE_SIZES[kind]) + code, value))


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
