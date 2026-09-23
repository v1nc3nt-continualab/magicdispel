"""HEIF, HEIC and AVIF (ISO base media files), cleaned in place.

Items (see bmff.py): metadata items (EXIF, URI property lists, JUMBF, and
MIME items other than XMP), editing-only auxiliary images (depth, mattes,
style maps, linear thumbnails) and thumbnails are removed with their bytes,
along with any tiles only they use. XMP items keep only HDR fields. Alpha,
HDR gain maps and everything the primary image needs stay; a file whose
displayed images need a removed item is refused. Item and handler names are
blanked and ICC profiles sanitized. HEIF orientation lives in irot/imir
properties, not in EXIF.

Boxes: top-level boxes other than ftyp, meta, moov and mdat become zero-filled
`free` boxes, and are dropped entirely at the end of the file; so do boxes in
meta other than the item tables; bytes in mdat and idat that no remaining item
or track sample uses are zeroed; in image sequences, creation and modification
times are cleared, handler and compressor names blanked, and user data,
metadata and uuid boxes emptied.

Media data never moves, so every item and sample offset stays valid.
"""
import struct

from .. import icc, xmp
from ..errors import FormatError, VerificationError
from . import bmff
from .bmff import StructureError, unsupported

KEPT = {b"ftyp", b"meta", b"moov", b"mdat"}
# Fragmented sequences keep samples outside moov; they are not supported.
REFUSED = {b"moof", b"mfra"}
# The item tables, and what they refer to: item data, data locations, image groups.
META_KEPT = {b"hdlr", b"pitm", b"iinf", b"iloc", b"iref", b"iprp", b"idat", b"dinf", b"grpl"}
EMPTIED = {b"udta", b"meta", b"uuid", b"free", b"skip"}
CONTAINERS = {b"moov", b"trak", b"edts", b"mdia", b"minf", b"dinf", b"stbl", b"mvex"}
TIMED = {b"mvhd", b"tkhd", b"mdhd"}
HANDLER_NAME = 24             # bytes of a handler box before its name
VISUAL_ENTRIES = {b"av01", b"hvc1", b"hev1", b"avc1", b"avc3"}
ENTRY_FIELDS = 78             # bytes of a visual sample entry before its child boxes
COMPRESSOR_NAME = (42, 74)    # within those fields
PROFILE_CONTAINERS = {b"iprp", b"ipco", b"trak", b"mdia", b"minf", b"stbl"}
# Coded, derived and tiled images. Metadata items are removed (XMP is reduced);
# any other item type is refused rather than guessed at.
IMAGE_ITEMS = {b"hvc1", b"av01", b"grid", b"iden", b"iovl", b"tmap", b"jpeg", b"avc1", b"hvt1",
               b"unci", b"vvc1", b"j2k1"}
METADATA_ITEMS = {b"Exif", b"uri ", b"mime", b"jumb"}
DISPLAY_AUXILIARIES = {
    b"urn:mpeg:hevc:2015:auxid:1",                    # alpha
    b"urn:mpeg:mpegB:cicp:systems:auxiliary:alpha",
    b"urn:com:apple:photo:2020:aux:hdrgainmap",
}
EDITING_AUXILIARIES = {
    b"urn:mpeg:hevc:2015:auxid:2",                    # depth
    b"urn:mpeg:mpegB:cicp:systems:auxiliary:depth",
    b"urn:com:apple:photo:2018:aux:portraiteffectsmatte",
    b"tag:apple.com,2023:photo:aux:linearthumbnail",
    b"tag:apple.com,2023:photo:aux:styledeltamap",
} | {b"urn:com:apple:photo:%s:aux:semantic%smatte" % (year, name)
     for year, name in ((b"2019", b"skin"), (b"2019", b"hair"), (b"2019", b"teeth"),
                        (b"2020", b"glasses"), (b"2020", b"sky"))}
# Reference types that may point to or from a removed item.
REMOVABLE_REFERENCES = {b"dimg", b"cdsc", b"auxl", b"thmb"}
MAX_XMP = 16 * 1024 * 1024
AVIF_BRANDS = {b"avif", b"avis"}
HEIF_BRANDS = {b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"hevm", b"hevs", b"mif1", b"msf1"}


def brand_format(data):
    """"AVIF", "HEIC" or None, from the file type box."""
    if data[4:8] != b"ftyp" or len(data) < 16:
        return None
    size = int.from_bytes(data[:4], "big")
    brands = {data[8:12]} | {data[n:n + 4] for n in range(16, min(size, len(data)) - 3, 4)}
    if brands & AVIF_BRANDS:
        return "AVIF"
    return "HEIC" if brands & HEIF_BRANDS else None


def rebuild(data):
    name = brand_format(data) or "HEIC"
    try:
        return cleaned(data)
    except icc.ProfileError as error:
        raise FormatError("unsupported_profile", format=name, detail=str(error))
    except StructureError as error:
        if error.damaged:
            raise FormatError("damaged", format=name)
        raise FormatError("unsupported_part", format=name, part=str(error))


def verify(original, rebuilt):
    """Check the result on its own terms, as listed in the module docstring."""
    try:
        check(original, rebuilt)
    except (StructureError, icc.ProfileError, xmp.XMPError) as error:
        fail(str(error))


def cleaned(data):
    top = top_boxes(data)
    if {found.kind for found in top} & REFUSED:
        raise unsupported("fragmented sequence")
    result = bytearray(data)
    layout = bmff.layout(data)
    if layout:
        clean_items(data, layout, result)
    for found in top:
        if found.kind in (b"meta", b"moov"):
            start = children(found)
            for a, b in list(profiles(result, start, found.end)):
                result[a:b] = icc.sanitize(bytes(result[a:b]))
            clean_boxes(result, start, found.end)
        elif found.kind not in KEPT:
            empty(result, found)
    for start, end in unused_media(result):
        result[start:end] = bytes(end - start)
    # Emptied boxes at the very end hold no offsets anyone needs.
    return bytes(result[:max(found.end for found in top if found.kind in KEPT)])


# ----------------------------------------------------------------------- items

def clean_items(data, layout, result):
    """Remove metadata, editing-only and thumbnail items with everything only
    they use, reduce XMP to HDR fields, and rewrite the item tables."""
    auxiliary = check_items(data, layout)
    seeds = ({ident for ident, item in layout.items.items() if item.kind in METADATA_ITEMS and not item.xmp}
             | {ident for ident, urn in auxiliary.items() if urn in EDITING_AUXILIARIES}
             | {origin for kind, origin, _ in layout.references if kind == b"thmb"}
             | {ident for ident, item in layout.items.items() if item.xmp and without_hdr_fields(data, layout, ident)})
    deleted = removed_items(layout, seeds)
    live = set(layout.items) - deleted
    retained = [span for ident in live for span in layout.extents[ident]]
    for ident in deleted:
        for start, end in layout.extents[ident]:
            if start < end and any(start < b and a < end for a, b in retained):
                raise unsupported("removed item shares data with a retained one")
            result[start:end] = bytes(end - start)
    for ident in live:
        if layout.items[ident].xmp:
            packet, position = hdr_xmp(data, layout, ident), 0
            for start, end in layout.extents[ident]:
                result[start:end] = packet[position:position + end - start]
                position += end - start
    rewrite_tables(data, layout, deleted, live, result)
    after = bmff.layout(result)
    if set(after.items) != live or after.primary != layout.primary:
        raise unsupported("item table rewrite did not verify")
    if any(data[start:end] != result[start:end] for ident in live if layout.items[ident].kind in IMAGE_ITEMS
           for start, end in layout.extents[ident]):
        raise unsupported("retained image data changed")


def check_items(data, layout):
    """Refuse items this module does not understand; {id: auxiliary type URN}."""
    unknown = {item.kind for item in layout.items.values()} - IMAGE_ITEMS - METADATA_ITEMS
    if unknown:
        raise unsupported("item type " + listed(unknown))
    auxiliary = bmff.auxiliary_types(data, layout)
    unknown = set(auxiliary.values()) - DISPLAY_AUXILIARIES - EDITING_AUXILIARIES
    if unknown:
        raise unsupported("auxiliary image " + listed(unknown))
    # Metadata items may only describe images (cdsc), and nothing may refer to them.
    metadata = {ident for ident, item in layout.items.items() if item.kind in METADATA_ITEMS}
    if any(metadata & set(targets) or (origin in metadata and kind != b"cdsc")
           for kind, origin, targets in layout.references):
        raise unsupported("metadata item reference")
    # Item data must lie in mdat or idat: that is where unused bytes are zeroed, and
    # nothing there is emptied.
    media = media_spans(layout.top, layout)
    if any(start < end and not any(a <= start and end <= b for a, b in media)
           for spans in layout.extents.values() for start, end in spans):
        raise unsupported("item data outside mdat and idat")
    return auxiliary


def removed_items(layout, seeds):
    """The seeds, the images only they are built from, and their descriptions."""
    candidates = with_sources(layout, seeds)
    needed = with_sources(layout, set(layout.items) - candidates)
    if layout.primary in candidates or seeds & needed:
        raise unsupported("an image needed for display depends on removed data")
    deleted = candidates - needed
    for kind, origin, targets in layout.references:
        if kind == b"cdsc" and targets and set(targets) <= deleted:  # describes only removed images
            if layout.items[origin].kind not in METADATA_ITEMS:
                raise unsupported("description of a removed image")
            deleted.add(origin)
    return deleted


def with_sources(layout, roots):
    """The roots and, recursively, every image a derived image (dimg) among them is built from."""
    found = set(roots)
    while True:
        grown = found | {target for kind, origin, targets in layout.references
                         if kind == b"dimg" and origin in found for target in targets}
        if grown == found:
            return found
        found = grown


def hdr_xmp(data, layout, ident):
    """An XMP item's packet reduced to its HDR fields and padded with spaces to
    its old length, so no offsets move; b"" if no field remains."""
    raw = b"".join(data[start:end] for start, end in layout.extents[ident])
    if len(raw) > MAX_XMP:
        raise unsupported("oversized XMP")
    try:
        packet = xmp.hdr_packet(xmp.hdr_fields(raw))
    except xmp.XMPError as error:
        raise unsupported("unreadable XMP: %s" % error)
    if len(packet) > len(raw):
        raise unsupported("reduced XMP exceeds its space")
    return packet and packet + b" " * (len(raw) - len(packet))


def without_hdr_fields(data, layout, ident):
    """Whether an XMP item has no HDR fields. An unreadable one only matters if
    the item is kept, and is refused then."""
    try:
        return not hdr_xmp(data, layout, ident)
    except StructureError:
        return False


def rewrite_tables(data, layout, deleted, live, result):
    """Write iinf, iloc, iref and iprp without the removed items, empty boxes
    other than the item tables, and compact the meta box."""
    entries = [entry for ident, entry in layout.location_entries.items() if ident in live]
    replacements = {
        layout.info.start: item_information(data, layout, live),
        layout.location.start: bmff.box(b"iloc", layout.location_prefix + len(entries).to_bytes(
            layout.location_width, "big") + b"".join(entries)),
    }
    if layout.reference_box:
        replacements[layout.reference_box.start] = item_references(data, layout, deleted, live)
    if layout.prop_box:
        replacements[layout.prop_box.start] = item_properties(data, layout, live)
    for child in layout.children:
        if child.kind == b"grpl":
            check_groups(data, child, deleted)
        elif child.kind not in META_KEPT:
            replacements[child.start] = blank(child)
    # idat must not move: construction-method-1 extents are relative to it.
    begin, parts = layout.meta.content + 4, []
    for child in layout.children:
        if child.kind == b"idat":
            fill(result, begin, child.start, parts)
            begin, parts = child.end, []
        else:
            parts.append(replacements.get(child.start, bytes(result[child.start:child.end])))
    fill(result, begin, layout.meta.end, parts)


def item_information(data, layout, live):
    """iinf with only the live items. Names are blanked, not shortened:
    shortening alone could leave a gap of 1 to 7 bytes, too small for a free box."""
    entries = []
    for ident, item in layout.items.items():
        if ident in live:
            name, terminator = item.name
            entries.append(bmff.box(b"infe", data[item.box.content:name] + b" " * (terminator - name)
                                    + data[terminator:item.box.end]))
    info = layout.info
    return bmff.box(b"iinf", data[info.content:info.content + 4]
                    + len(entries).to_bytes(layout.count_size, "big") + b"".join(entries))


def item_references(data, layout, deleted, live):
    """iref without references from removed items, or to them."""
    iref = layout.reference_box
    width = 2 if data[iref.content] == 0 else 4
    entries = []
    for kind, origin, targets in layout.references:
        if origin in deleted or deleted & set(targets):
            if kind not in REMOVABLE_REFERENCES:
                raise unsupported("unknown dependency on removed data")
            if origin in deleted:
                continue
            if kind == b"dimg":
                raise unsupported("a retained image lost a dependency")
            targets = [target for target in targets if target in live]
            if not targets:
                continue
        entries.append(bmff.box(kind, origin.to_bytes(width, "big") + len(targets).to_bytes(2, "big")
                                + b"".join(target.to_bytes(width, "big") for target in targets)))
    return bmff.box(b"iref", data[iref.content:iref.content + 4] + b"".join(entries))


def item_properties(data, layout, live):
    """iprp with associations for the live items only, and unused properties
    turned into free boxes, which keeps every property's index."""
    used = {index for ident in live for index in layout.associations.get(ident, [])}
    parts = []
    for part in bmff.boxes(data, layout.prop_box.content, layout.prop_box.end):
        if part.kind == b"ipco":
            parts.append(bmff.box(b"ipco", b"".join(bytes(data[prop.start:prop.end]) if index in used else blank(prop)
                                                    for index, prop in layout.props.items())))
        elif part.kind == b"ipma":
            entries = next(entries for found, entries in layout.association_boxes if found == part)
            kept = [entry for ident, entry in entries if ident in live]
            parts.append(bmff.box(b"ipma", data[part.content:part.content + 4]
                                  + len(kept).to_bytes(4, "big") + b"".join(kept)))
        else:
            parts.append(blank(part))
    return bmff.box(b"iprp", b"".join(parts))


def check_groups(data, grpl, deleted):
    """An alternative-image group may not lose a member: it could be shown instead."""
    for group in bmff.boxes(data, grpl.content, grpl.end):
        if group.kind != b"altr" or data[group.content:group.content + 4] != bytes(4) or group.end - group.content < 12:
            raise unsupported("image group")
        count = int.from_bytes(data[group.content + 8:group.content + 12], "big")
        if group.content + 12 + 4 * count != group.end:
            raise StructureError("invalid image group")
        if any(int.from_bytes(data[p:p + 4], "big") in deleted for p in range(group.content + 12, group.end, 4)):
            raise unsupported("a removed image is an alternative display image")


def blank(found):
    """A zero-filled free box the size of `found`."""
    return bmff.box(b"free", bytes(found.end - found.start - 8))


def fill(result, start, end, parts):
    """Write `parts` from `start`, and a free box over the rest of the space up to `end`."""
    payload = b"".join(parts)
    gap = end - start - len(payload)
    if gap < 0 or 0 < gap < 8:  # a free box needs its 8-byte header
        raise unsupported("no room to rewrite the item tables")
    result[start:end] = payload + (bmff.box(b"free", bytes(gap - 8)) if gap else b"")


def item_profiles(data, layout, ident):
    """The ICC profiles of the colr properties associated with an item."""
    found = []
    for index in layout.associations.get(ident, []):
        if index:
            prop = layout.props[index]
            if prop.kind == b"colr" and data[prop.content:prop.content + 4] in (b"prof", b"rICC"):
                found.append(data[prop.content + 4:prop.end])
    return found


def profiles(data, start, end):
    """(start, end) of the ICC profile in each colr box among item properties,
    or in the sample entries of sequence tracks, from a meta or moov box's children."""
    for found in bmff.boxes(data, start, end):
        if found.kind == b"colr" and data[found.content:found.content + 4] in (b"prof", b"rICC"):
            yield found.content + 4, found.end
        elif found.kind in PROFILE_CONTAINERS:
            yield from profiles(data, found.content, found.end)
        elif found.kind == b"stsd":
            for entry in bmff.boxes(data, found.content + 8, found.end):
                if entry.kind in VISUAL_ENTRIES:
                    yield from profiles(data, entry.content + ENTRY_FIELDS, entry.end)


# ----------------------------------------------------------------------- boxes

def top_boxes(data):
    found = list(bmff.boxes(data))
    if not found or found[0].kind != b"ftyp":
        raise StructureError("no file type box")
    return found


def children(found):
    """Where the child boxes of a meta or moov box start. meta is a full box:
    a version and flags come first."""
    return found.content + (4 if found.kind == b"meta" else 0)


def empty(buffer, found):
    """Turn a box into a zero-filled free box of the same size, in place."""
    buffer[found.start + 4:found.start + 8] = b"free"
    buffer[found.content:found.end] = bytes(found.end - found.content)


def clean_boxes(buffer, start, end):
    """Empty user data, metadata and uuid boxes, and clear times, names and
    data reference locations, in the boxes from start to end and below."""
    for found in bmff.boxes(buffer, start, end):
        if found.kind in EMPTIED:
            empty(buffer, found)
        elif found.kind in CONTAINERS:
            clean_boxes(buffer, found.content, found.end)
        else:
            for a, b in cleared_fields(buffer, found):
                buffer[a:b] = bytes(b - a)


def check_boxes(data, start, end):
    for found in bmff.boxes(data, start, end):
        if found.kind in EMPTIED - {b"free"}:
            fail("user data or metadata box kept")
        if not zeroed(data, cleared_fields(data, found)) or (found.kind == b"free"
                                                             and not zeroed(data, [(found.content, found.end)])):
            fail("names or times kept")
        if found.kind in CONTAINERS:
            check_boxes(data, found.content, found.end)


def cleared_fields(data, found):
    """The spans of a box that are cleared: creation and modification times,
    handler and compressor names, data reference locations."""
    if found.kind in TIMED:
        size = 16 if data[found.content] == 1 else 8  # version 1 has 64-bit times
        if found.end - found.content < 4 + size:
            raise StructureError("truncated movie header")
        return [(found.content + 4, found.content + 4 + size)]
    if found.kind == b"hdlr":
        return [(found.content + HANDLER_NAME, found.end)] if found.end - found.content > HANDLER_NAME else []
    if found.kind == b"stsd":
        first, last = COMPRESSOR_NAME
        return [(entry.content + first, entry.content + last) for entry in bmff.boxes(data, found.content + 8, found.end)
                if entry.kind in VISUAL_ENTRIES and entry.end - entry.content >= ENTRY_FIELDS]
    if found.kind == b"dref":
        return data_references(data, found)
    return []


def data_references(data, dref):
    """Media must be in this file: each url/urn entry is self-contained. Any
    location text after an entry's flags is returned for clearing."""
    spans = []
    for entry in bmff.boxes(data, dref.content + 8, dref.end):
        if entry.kind not in (b"url ", b"urn ") or entry.end - entry.content < 4 or not data[entry.content + 3] & 1:
            raise unsupported("external media reference")
        spans.append((entry.content + 4, entry.end))
    return spans


def unused_media(data):
    """Spans of mdat and idat that no remaining item or track sample uses."""
    top, layout = top_boxes(data), bmff.layout(data)
    used = [span for spans in layout.extents.values() for span in spans] if layout else []
    for found in top:
        if found.kind == b"moov":
            used += [span for table in sample_tables(data, found.content, found.end)
                     for span in sample_ranges(data, table)]
    used.sort()
    return [gap for start, end in media_spans(top, layout) for gap in gaps(used, start, end)]


def media_spans(top, layout):
    """Content spans of the boxes holding item and sample data: mdat and idat."""
    spans = [(found.content, found.end) for found in top if found.kind == b"mdat"]
    return spans + [layout.idat] if layout and layout.idat else spans


def sample_tables(data, start, end):
    for found in bmff.boxes(data, start, end):
        if found.kind == b"stbl":
            yield found
        elif found.kind in (b"trak", b"mdia", b"minf"):
            yield from sample_tables(data, found.content, found.end)


def sample_ranges(data, stbl):
    """(start, end) of each chunk of samples, from stsc, stsz and stco/co64."""
    parts = {found.kind: found.content for found in bmff.boxes(data, stbl.content, stbl.end)}
    try:
        fixed, count = struct.unpack_from(">II", data, parts[b"stsz"] + 4)
        sizes = [fixed] * count if fixed else list(struct.unpack_from(">%dI" % count, data, parts[b"stsz"] + 12))
        runs = [struct.unpack_from(">III", data, parts[b"stsc"] + 8 + 12 * n)[:2]
                for n in range(struct.unpack_from(">I", data, parts[b"stsc"] + 4)[0])]
        wide = b"co64" in parts
        table = parts[b"co64" if wide else b"stco"]
        chunks = struct.unpack_from(">%d%s" % (struct.unpack_from(">I", data, table + 4)[0], "Q" if wide else "I"),
                                    data, table + 8)
    except (KeyError, struct.error):
        raise unsupported("sample table")
    ranges, sample = [], 0
    for index, offset in enumerate(chunks, 1):
        # stsc runs: from chunk `first` on, each chunk holds `samples` samples.
        per_chunk = next((samples for first, samples in reversed(runs) if first <= index), 0)
        ranges.append((offset, offset + sum(sizes[sample:sample + per_chunk])))
        sample += per_chunk
    if sample != len(sizes):
        raise StructureError("sample table does not match its samples")
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


def listed(kinds):
    return ", ".join(sorted(kind.decode("latin-1") for kind in kinds))


# ---------------------------------------------------------------- verification

def check(original, rebuilt):
    top = top_boxes(rebuilt)
    for found in top:
        if found.kind not in KEPT and not (found.kind == b"free" and zeroed(rebuilt, [(found.content, found.end)])):
            fail("unexpected box %r" % found.kind)
    if rebuilt[:top[0].end] != original[:top[0].end]:
        fail("file type box changed")
    before, after = bmff.layout(original), bmff.layout(rebuilt)
    if (before is None) != (after is None) or (after and after.primary != before.primary):
        fail("primary image changed")
    if after:
        check_cleaned_items(original, before, rebuilt, after)
    if not zeroed(rebuilt, unused_media(rebuilt)):
        fail("unused media data kept")
    for found in top:
        if found.kind in (b"meta", b"moov"):
            check_boxes(rebuilt, children(found), found.end)
        if found.kind == b"moov":
            # The movie box never moves, so its profiles are matched by position.
            for start, end in profiles(rebuilt, found.content, found.end):
                if rebuilt[start:end] != icc.sanitize(original[start:end]):
                    fail("track color profile not sanitized")


def check_cleaned_items(original, before, rebuilt, after):
    for child in after.children:
        if child.kind not in META_KEPT and not (child.kind == b"free" and zeroed(rebuilt, [(child.content, child.end)])):
            fail("unexpected item box %r" % child.kind)
    if not set(after.items) <= set(before.items):
        fail("unexpected item")
    auxiliary = bmff.auxiliary_types(rebuilt, after)
    if set(auxiliary.values()) - DISPLAY_AUXILIARIES or any(kind == b"thmb" for kind, _, _ in after.references):
        fail("editing image or thumbnail kept")
    for ident, item in after.items.items():
        content = b"".join(rebuilt[a:b] for a, b in after.extents[ident])
        if item.xmp:
            packet = content.rstrip(b" ")
            if xmp.hdr_packet(xmp.hdr_fields(packet)) != packet:
                fail("XMP holds more than HDR fields")
        elif item.kind not in IMAGE_ITEMS:
            fail("metadata item kept")
        elif content != b"".join(original[a:b] for a, b in before.extents[ident]):
            fail("image item %d changed" % ident)
        if rebuilt[item.name[0]:item.name[1]].strip(b" "):
            fail("item name kept")
        # Item tables may be compacted, so item profiles are matched by item.
        if item_profiles(rebuilt, after, ident) != [icc.sanitize(profile)
                                                    for profile in item_profiles(original, before, ident)]:
            fail("color profile of item %d not sanitized" % ident)


def fail(detail):
    raise VerificationError("verification_failed", detail=detail)
