"""HEIF, HEIC and AVIF (ISO base media files), cleaned in place.

Item-level work is done by privacy.py: EXIF and URI metadata items,
editing-only auxiliary images (depth, mattes, style maps) and thumbnails go
with their bytes, XMP items keep only HDR fields, item names are blanked and
ICC profiles are sanitized. On top of that, at the box level:

- top-level boxes other than ftyp, meta, moov and mdat become zero-filled
  `free` boxes, and are dropped entirely at the end of the file;
- bytes in mdat that no remaining item or track sample uses are zeroed;
- in image sequences, creation and modification times are cleared, handler
  and compressor names blanked, and user data, metadata and uuid boxes are
  emptied the same way.

Every box keeps its size, so all item and sample offsets stay valid.
"""
import struct

from .. import xmp
from ..errors import FormatError, VerificationError
from ..privacy import (PrivacyError, bmff_boxes, heif_layout, sanitize_bmff_profiles, sanitize_icc,
                       strip_heif_auxiliary, strip_heif_private)

KEPT = {b"ftyp", b"meta", b"moov", b"mdat"}
# Fragmented sequences keep samples outside moov; they are not supported.
REFUSED = {b"moof", b"mfra"}
EMPTIED = {b"udta", b"meta", b"uuid", b"free", b"skip"}
CONTAINERS = {b"moov", b"trak", b"edts", b"mdia", b"minf", b"dinf", b"stbl", b"mvex"}
TIMED = {b"mvhd", b"tkhd", b"mdhd"}
VISUAL_ENTRIES = {b"av01", b"hvc1", b"hev1", b"avc1", b"avc3"}
# Coded, derived and tiled images. Metadata items are removed (XMP is reduced);
# any other item type is refused rather than guessed at.
IMAGE_ITEMS = {b"hvc1", b"av01", b"grid", b"iden", b"iovl", b"tmap", b"jpeg", b"avc1", b"hvt1",
               b"unci", b"vvc1", b"j2k1"}
METADATA_ITEMS = {b"Exif", b"uri ", b"mime", b"jumb"}
AVIF_BRANDS = {b"avif", b"avis"}


def brand_format(data):
    """"AVIF", "HEIC" or None, from the file type box."""
    if data[4:8] != b"ftyp" or len(data) < 16:
        return None
    size = int.from_bytes(data[:4], "big")
    brands = {data[8:12]} | {data[n:n + 4] for n in range(16, min(size, len(data)) - 3, 4)}
    if brands & AVIF_BRANDS:
        return "AVIF"
    if brands & {b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"hevm", b"hevs", b"mif1", b"msf1"}:
        return "HEIC"
    return None


def rebuild(data):
    name = brand_format(data) or "HEIC"
    kinds = {kind for kind, *_ in boxes(data, name)}
    if kinds & REFUSED:
        raise FormatError("unsupported_part", format=name, part="fragmented sequence")
    try:
        layout = heif_layout(data)
        unknown = {item["type"] for item in layout["items"].values()} - IMAGE_ITEMS - METADATA_ITEMS \
            if layout else set()
        if unknown:
            raise PrivacyError("item type " + ", ".join(sorted(t.decode("latin-1") for t in unknown)))
        trimmed, _ = sanitize_bmff_profiles(strip_heif_auxiliary(strip_heif_private(data)))
    except PrivacyError as error:
        raise FormatError("unsupported_part", format=name, part=str(error))
    result = bytearray(trimmed)
    top = boxes(result, name)
    for kind, start, content, end in top:
        if kind == b"moov":
            clean_movie(result, content, end, name)
        elif kind == b"meta":
            for child, child_start, child_content, child_end in bmff_boxes(result, content + 4, end):
                if child == b"dinf":
                    clean_movie(result, child_content, child_end, name)
        elif kind not in KEPT:
            empty(result, start, content, end)
    zero_unused_media(result, name)
    # Emptied boxes at the very end hold no offsets anyone needs.
    return bytes(result[:max(end for kind, _, _, end in top if kind in KEPT)])


def verify(original, rebuilt):
    """Check the result on its own terms, as listed in the module docstring."""
    try:
        check(original, rebuilt)
    except PrivacyError as error:
        fail(str(error))


def check(original, rebuilt):
    name = brand_format(original) or "HEIC"
    top = boxes(rebuilt, name)
    for kind, start, content, end in top:
        if kind not in KEPT and not (kind == b"free" and zeroed(rebuilt, [(content, end)])):
            fail("unexpected %s box %r" % (name, kind))
    if top[0][0] != b"ftyp" or rebuilt[:top[0][3]] != original[:top[0][3]]:
        fail("file type box changed")
    before, after = heif_layout(original), heif_layout(rebuilt)
    if after is None or after["primary"] != before["primary"]:
        fail("primary image changed")
    for ident, item in after["items"].items():
        if item["type"] not in IMAGE_ITEMS and not item["xmp"]:
            fail("metadata item kept")
        content = b"".join(rebuilt[a:b] for a, b in after["extents"][ident][1])
        if item["xmp"]:
            packet = content.rstrip(b" ")
            if xmp.hdr_packet(xmp.hdr_fields(packet)) != packet:
                fail("XMP holds more than HDR fields")
        elif content != b"".join(original[a:b] for a, b in before["extents"][ident][1]):
            fail("image item %d changed" % ident)
    # Item tables may be compacted, so item profiles are matched by item; the
    # movie box never moves, so track profiles are matched by position.
    for ident in after["items"]:
        if item_profiles(rebuilt, after, ident) != [sanitize_icc(profile)
                                                    for profile in item_profiles(original, before, ident)]:
            fail("color profile of item %d not sanitized" % ident)
    for start, end in track_profiles(rebuilt):
        if rebuilt[start:end] != sanitize_icc(original[start:end]):
            fail("track color profile not sanitized")
    used = used_ranges(rebuilt, name)
    for start, end in data_boxes(rebuilt, name):
        if not zeroed(rebuilt, gaps(used, start, end)):
            fail("unused media data kept")
    for kind, start, content, end in top:
        if kind == b"moov":
            check_movie(rebuilt, content, end)
        if kind == b"meta":
            for child, _, child_content, child_end in bmff_boxes(rebuilt, content + 4, end):
                if child == b"dinf":
                    check_movie(rebuilt, child_content, child_end)


def boxes(data, name):
    try:
        found = list(bmff_boxes(data))
    except PrivacyError:
        raise FormatError("damaged", format=name)
    if not found or found[0][0] != b"ftyp":
        raise FormatError("damaged", format=name)
    return found


def empty(buffer, start, content, end):
    """Turn a box into a zero-filled free box of the same size."""
    buffer[start + 4:start + 8] = b"free"
    buffer[content:end] = bytes(end - content)


def clean_movie(buffer, start, end, name):
    """Clear what describes the recording in a sequence's movie box."""
    for kind, box_start, content, box_end in bmff_boxes(buffer, start, end):
        if kind in EMPTIED:
            empty(buffer, box_start, content, box_end)
        elif kind in TIMED:
            for a, b in time_fields(buffer, content, box_end, name):
                buffer[a:b] = bytes(b - a)
        elif kind == b"hdlr" and box_end - content > 24:
            buffer[content + 24:box_end] = bytes(box_end - content - 24)  # handler name
        elif kind == b"stsd":
            for a, b in compressor_names(buffer, content, box_end):
                buffer[a:b] = bytes(b - a)
        elif kind == b"dref":
            for a, b in data_references(buffer, content, box_end, name):
                buffer[a:b] = bytes(b - a)
        elif kind in CONTAINERS:
            clean_movie(buffer, content, box_end, name)


def check_movie(data, start, end):
    for kind, box_start, content, box_end in bmff_boxes(data, start, end):
        if kind in EMPTIED - {b"free"}:
            fail("movie metadata kept")
        cleared = (time_fields(data, content, box_end, "HEIC") if kind in TIMED
                   else [(content + 24, box_end)] if kind == b"hdlr"
                   else compressor_names(data, content, box_end) if kind == b"stsd"
                   else data_references(data, content, box_end, "HEIC") if kind == b"dref" else [])
        if not zeroed(data, cleared) or (kind == b"free" and not zeroed(data, [(content, box_end)])):
            fail("movie names or times kept")
        if kind in CONTAINERS:
            check_movie(data, content, box_end)


def time_fields(data, content, end, name):
    """Creation and modification times of mvhd, tkhd and mdhd."""
    wide = data[content] == 1
    if end - content < (20 if wide else 12):
        raise FormatError("damaged", format=name)
    return [(content + 4, content + 20)] if wide else [(content + 4, content + 12)]


def compressor_names(data, content, end):
    """The 32-byte compressor name of each visual sample entry."""
    names = []
    for kind, _, entry, entry_end in bmff_boxes(data, content + 8, end):
        if kind in VISUAL_ENTRIES and entry_end - entry >= 78:
            names.append((entry + 42, entry + 74))
    return names


def data_references(data, content, end, name):
    """Media must be in this file: each url/urn entry is self-contained, and
    any location text after its flags is returned for clearing."""
    spans = []
    for kind, _, entry, entry_end in bmff_boxes(data, content + 8, end):
        if kind not in (b"url ", b"urn ") or entry_end - entry < 4 or not data[entry + 3] & 1:
            raise FormatError("unsupported_part", format=name, part="external media reference")
        spans.append((entry + 4, entry_end))
    return spans


def item_profiles(data, layout, ident):
    """The ICC profiles of the colr properties associated with an item."""
    found = []
    for index in layout["associations"].get(ident, []):
        if index:
            kind, _, content, end = layout["props"][index]
            if kind == b"colr" and data[content:content + 4] in (b"prof", b"rICC"):
                found.append(data[content + 4:end])
    return found


def track_profiles(data):
    """Byte ranges of the ICC profiles in the sample entries of sequence tracks."""
    found = []

    def walk(start, end):
        for kind, _, content, box_end in bmff_boxes(data, start, end):
            if kind == b"colr" and data[content:content + 4] in (b"prof", b"rICC"):
                found.append((content + 4, box_end))
            elif kind in {b"moov", b"trak", b"mdia", b"minf", b"stbl"}:
                walk(content, box_end)
            elif kind == b"stsd":
                for sample, _, entry, entry_end in bmff_boxes(data, content + 8, box_end):
                    if sample in VISUAL_ENTRIES:
                        walk(entry + 78, entry_end)
    for kind, _, content, end in bmff_boxes(data):
        if kind == b"moov":
            walk(content, end)
    return found


def zero_unused_media(buffer, name):
    used = used_ranges(buffer, name)
    for start, end in data_boxes(buffer, name):
        for a, b in gaps(used, start, end):
            buffer[a:b] = bytes(b - a)


def data_boxes(data, name):
    """Content ranges of the boxes holding item and sample data: mdat and idat."""
    ranges = [(content, end) for kind, _, content, end in boxes(data, name) if kind == b"mdat"]
    layout = heif_layout(bytes(data))
    if layout and layout["idat"]:
        ranges.append(layout["idat"])
    return ranges


def used_ranges(data, name):
    """Byte ranges that remaining items and track samples point to."""
    layout = heif_layout(bytes(data))
    ranges = [span for _, spans in layout["extents"].values() for span in spans] if layout else []
    for kind, _, content, end in boxes(data, name):
        if kind == b"moov":
            for table in sample_tables(data, content, end):
                ranges.extend(sample_ranges(data, table, name))
    return sorted(ranges)


def sample_tables(data, start, end):
    for kind, _, content, box_end in bmff_boxes(data, start, end):
        if kind == b"stbl":
            yield content, box_end
        elif kind in (b"trak", b"mdia", b"minf"):
            yield from sample_tables(data, content, box_end)


def sample_ranges(data, table, name):
    """(start, end) of each chunk of samples, from stsc, stsz and stco/co64."""
    parts = {kind: (content, end) for kind, _, content, end in bmff_boxes(data, *table)}
    try:
        start, _ = parts[b"stsz"]
        fixed, count = struct.unpack_from(">II", data, start + 4)
        sizes = [fixed] * count if fixed else list(struct.unpack_from(">%dI" % count, data, start + 12))
        start, _ = parts[b"stsc"]
        runs = [struct.unpack_from(">III", data, start + 8 + 12 * n)[:2]
                for n in range(struct.unpack_from(">I", data, start + 4)[0])]
        wide = b"co64" in parts
        start, _ = parts[b"co64" if wide else b"stco"]
        chunks = struct.unpack_from(">%d%s" % (struct.unpack_from(">I", data, start + 4)[0], "Q" if wide else "I"),
                                    data, start + 8)
    except (KeyError, struct.error):
        raise FormatError("unsupported_part", format=name, part="sample table")
    ranges, sample = [], 0
    for index, offset in enumerate(chunks, 1):
        per_chunk = next((samples for first, samples in reversed(runs) if first <= index), 0)
        ranges.append((offset, offset + sum(sizes[sample:sample + per_chunk])))
        sample += per_chunk
    if sample != len(sizes):
        raise FormatError("damaged", format=name)
    return ranges


def gaps(used, start, end):
    """Parts of [start, end) that no used range covers."""
    position = start
    for a, b in used:
        if b <= position or a >= end:
            continue
        if a > position:
            yield position, a
        position = max(position, b)
    if position < end:
        yield position, end


def zeroed(data, spans):
    """Whether every byte in the given (start, end) spans is zero."""
    return not any(any(data[a:b]) for a, b in spans)


def fail(detail):
    raise VerificationError("verification_failed", detail=detail)
