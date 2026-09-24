"""HEIF, HEIC and AVIF (ISO base media files), cleaned in place.

Items (see bmff.py): metadata items (EXIF, URI property lists, JUMBF, and
MIME items other than XMP), editing-only auxiliary images (depth, mattes,
style maps, linear thumbnails) and thumbnails are removed with their bytes,
along with any tiles only they use. XMP items keep only HDR fields. Alpha,
HDR gain maps and everything the primary image needs stay; a file whose
displayed images need a removed item is refused, and so is gain-map metadata
(tmap) in any but its exact standard layout. Item and handler names are
blanked and ICC profiles sanitized. HEIF orientation lives in irot/imir
properties, not in EXIF.

Item properties: only those that say how to decode and show an image are
kept, fixed-size ones at exactly their size. Descriptions, creation and
modification times, camera parameters and unknown properties go; an unknown
property marked essential, which a reader may not ignore, makes the file
unsupported.

Boxes: top-level boxes other than ftyp, meta, moov and mdat become zero-filled
`free` boxes, and are dropped entirely at the end of the file; so do boxes in
meta other than the item tables; bytes in mdat and idat that no remaining item
or track sample uses are zeroed. Image sequences keep only the boxes that play
them, and only picture tracks and their alpha (see SEQUENCE and movie.py).

Media data never moves, so every item and sample offset stays valid.
"""
from .. import gainmap, icc, xmp
from ..errors import FormatError, VerificationError
from . import bmff, configs, movie
from .bmff import METADATA_ITEMS, StructureError, unsupported
from .movie import empty, gaps, zeroed

KEPT = {b"ftyp", b"meta", b"moov", b"mdat"}
# Fragmented sequences keep samples outside moov; they are not supported.
REFUSED = {b"moof", b"mfra"}
# The item tables, and what they refer to: item data, data locations, image groups.
META_KEPT = {b"hdlr", b"pitm", b"iinf", b"iloc", b"iref", b"iprp", b"idat", b"dinf", b"grpl"}
EMPTIED = {b"udta", b"meta", b"uuid", b"free", b"skip"}
CONTAINERS = {b"dinf"}  # boxes in meta whose own boxes are cleaned too
# What an image sequence keeps, by container: headers, tracks and their
# references and edits, and the sample tables (ISO/IEC 14496-12); the tracks
# it is shown from, its pictures and auxiliary tracks such as alpha; and in
# their sample entries the boxes that say how to decode and show the samples.
VISUAL_ENTRIES = {b"av01", b"hvc1", b"hev1", b"avc1", b"avc3"}
ENTRY_BOXES = dict.fromkeys({b"av1C", b"hvcC", b"avcC", b"lhvC", b"colr", b"pasp", b"clap", b"btrt", b"ccst",
                             b"auxi", b"mdcv", b"clli", b"amve", b"fiel"})
SEQUENCE = movie.Policy(
    boxes={
        b"moov": {b"mvhd", b"trak"},
        b"trak": {b"tkhd", b"tref", b"edts", b"mdia"},
        b"edts": {b"elst"},
        b"mdia": {b"mdhd", b"hdlr", b"minf"},
        b"minf": {b"vmhd", b"nmhd", b"dinf", b"stbl"},
        b"dinf": {b"dref"},
        b"stbl": {b"stsd", b"stts", b"ctts", b"cslg", b"stsc", b"stsz", b"stco", b"co64", b"stss", b"sdtp",
                  b"sbgp", b"sgpd"},
    },
    entries={handler: (VISUAL_ENTRIES, ENTRY_BOXES) for handler in (b"pict", b"vide", b"auxv")},
    thumbnails=True)
# Coded, derived and tiled images. Metadata items are removed (XMP is reduced);
# any other item type is refused rather than guessed at.
IMAGE_ITEMS = {b"hvc1", b"av01", b"grid", b"iden", b"iovl", b"tmap", b"jpeg", b"avc1", b"hvt1",
               b"unci", b"vvc1", b"j2k1"}
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
# Item properties needed to decode and show an image: decoder configurations;
# size, layout and orientation; color and HDR; the role of an auxiliary image.
DISPLAY_PROPERTIES = {
    b"hvcC", b"av1C", b"avcC", b"vvcC", b"jpgC", b"j2kH", b"uncC", b"cmpd", b"lhvC", b"oinf", b"tols",
    b"ispe", b"pixi", b"clap", b"irot", b"imir", b"pasp", b"rloc", b"iscl", b"a1op", b"a1lx", b"lsel",
    b"colr", b"clli", b"mdcv", b"cclv", b"amve",
    b"auxC",
}
# Descriptive properties: user descriptions, creation and modification times,
# accessibility text, camera intrinsics and extrinsics, clock information.
DESCRIPTIVE_PROPERTIES = {b"udes", b"crtt", b"mdft", b"altt", b"cmin", b"cmex", b"taic", b"itai"}
# Payload sizes of fixed-size properties, which then cannot carry extra bytes.
PROPERTY_SIZES = {b"ispe": 12, b"irot": 1, b"imir": 1, b"pasp": 8, b"clap": 32, b"clli": 4, b"mdcv": 24,
                  b"a1op": 1, b"lsel": 2}
# Reference types that may point to or from a removed item.
REMOVABLE_REFERENCES = {b"dimg", b"cdsc", b"auxl", b"thmb"}
MAX_XMP = 16 * 1024 * 1024
AVIF_BRANDS = {b"avif", b"avis"}
HEIF_BRANDS = {b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"hevm", b"hevs", b"mif1", b"msf1"}
# The other brands of HEIF files that say how to read them: MIAF and its
# profiles, AVIF's profiles, gain maps, ISO. Other compatible brands are
# cleared (see bmff.unknown_brands).
KNOWN_BRANDS = AVIF_BRANDS | HEIF_BRANDS | {b"miaf", b"MiHA", b"MiHB", b"MiHE", b"MiAn", b"MiPr", b"MiCm",
                                            b"MA1A", b"MA1B", b"avio", b"tmap", b"mif2", b"iso8", b"unif",
                                            # images and sequences of the other codecs of IMAGE_ITEMS
                                            b"avci", b"avcs", b"jpeg", b"jpgs", b"vvic", b"vvis", b"j2ki", b"j2is"}


def brand_format(data):
    """"AVIF", "HEIC" or None, from the file type box."""
    brands = set(bmff.brands(data))
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
    except RecursionError:  # boxes nested beyond any real file
        raise FormatError("damaged", format=name)


def verify(original, rebuilt):
    """Check the result on its own terms, as listed in the module docstring."""
    try:
        check(original, rebuilt)
    except (StructureError, icc.ProfileError, xmp.XMPError, RecursionError) as error:
        fail(str(error) or type(error).__name__)


def cleaned(data):
    top = top_boxes(data)
    if {found.kind for found in top} & REFUSED:
        raise unsupported("fragmented sequence")
    result = bytearray(data)
    movie.clear(result, file_type_brands(data, top[0]))
    kept = kept_boxes(top)
    layout = bmff.layout(data)
    if layout:  # items share no data with the samples a sequence keeps, nor with each other
        clean_items(data, layout, result, [span for found in top if found.kind == b"moov"
                                           for span in movie.kept_ranges(data, found, SEQUENCE)])
    for found in top:
        if found.kind == b"meta":
            for a, b in list(profiles(result, children(found), found.end)):
                result[a:b] = icc.sanitize(bytes(result[a:b]))
            clean_boxes(result, children(found), found.end)
        elif found.kind == b"moov":
            movie.clean(result, found, SEQUENCE)
        elif found not in kept:
            empty(result, found)
    for start, end in unused_media(result):
        result[start:end] = bytes(end - start)
    # Emptied boxes at the very end hold no offsets anyone needs.
    return bytes(result[:max(found.end for found in kept)])


def file_type_brands(data, ftyp):
    """The compatible brands cleared from the file type box (see bmff.unknown_brands)."""
    return bmff.unknown_brands(data, ftyp, KNOWN_BRANDS, KNOWN_BRANDS)


def kept_boxes(top):
    """The top-level boxes kept: the file type box that comes first, meta, moov and mdat."""
    return {found for found in top if found.kind in KEPT and (found.kind != b"ftyp" or found is top[0])}


# ----------------------------------------------------------------------- items

def clean_items(data, layout, result, samples):
    """Remove metadata, editing-only and thumbnail items with everything only
    they use, reduce XMP to HDR fields, and rewrite the item tables. No item
    removed may share bytes with a kept one or with the kept `samples`."""
    auxiliary = check_items(data, layout)
    seeds = ({ident for ident, item in layout.items.items() if item.kind in METADATA_ITEMS and not item.xmp}
             | {ident for ident, urn in auxiliary.items() if urn in EDITING_AUXILIARIES}
             | {origin for kind, origin, _ in layout.references if kind == b"thmb"}
             | {ident for ident, item in layout.items.items() if item.xmp and without_hdr_fields(data, layout, ident)})
    deleted = removed_items(layout, seeds)
    live = set(layout.items) - deleted
    retained = movie.merged([span for ident in live for span in layout.extents[ident]] + samples)
    for ident in deleted:
        for start, end in layout.extents[ident]:
            if movie.overlaps(retained, start, end):
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
    check_tone_maps(data, layout)
    return auxiliary


def check_tone_maps(data, layout):
    """A tone-mapped image (tmap) holds a version byte and ISO 21496-1 gain-map
    metadata; its data is kept as it is, so it must be exactly that."""
    for ident, item in layout.items.items():
        if item.kind == b"tmap":
            payload = b"".join(data[start:end] for start, end in layout.extents[ident])
            try:
                if payload[:1] != b"\0":
                    raise gainmap.GainMapError("tone map version")
                if gainmap.size(payload[1:], full=True) != len(payload) - 1:
                    raise gainmap.GainMapError("data after the metadata")
            except gainmap.GainMapError as error:
                raise unsupported("gain map metadata, %s" % error)


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
        replacements[layout.prop_box.start] = item_properties(data, layout, live, kept_properties(data, layout, live))
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


def kept_properties(data, layout, live):
    """The indices of the properties the live items keep: those needed to show them."""
    kept = set()
    for ident in live:
        for index in layout.associations.get(ident, []):
            prop = layout.props.get(index)  # index 0 means no property
            if prop and prop.kind in DISPLAY_PROPERTIES:
                check_size(data, prop)
                kept.add(index)
            elif prop and index in layout.essential[ident] and prop.kind not in DESCRIPTIVE_PROPERTIES:
                raise unsupported("item property " + listed([prop.kind]))
    return kept


def check_size(data, prop):
    """A fixed-size property must have exactly its size, and a decoder
    configuration end where it says (see configs.py)."""
    configs.check(prop.kind, data, prop.content, prop.end)
    size = prop.end - prop.content
    if prop.kind == b"pixi":  # a version and flags, the channel count, a depth per channel
        expected = 5 + data[prop.content + 4] if size > 4 else 0
    elif prop.kind == b"colr" and data[prop.content:prop.content + 4] == b"nclx":
        expected = 11  # primaries, transfer, matrix and range
    else:
        expected = PROPERTY_SIZES.get(prop.kind, size)
    if size != expected:
        raise unsupported("extra data in item property " + listed([prop.kind]))


def item_properties(data, layout, live, kept):
    """iprp with every property but the kept ones turned into a free box, which
    keeps each property's index. A live item's other associations become index
    0, "no property", so the tables keep their size."""
    parts = []
    for part in bmff.boxes(data, layout.prop_box.content, layout.prop_box.end):
        if part.kind == b"ipco":
            parts.append(bmff.box(b"ipco", b"".join(bytes(data[prop.start:prop.end]) if index in kept else blank(prop)
                                                    for index, prop in layout.props.items())))
        elif part.kind == b"ipma":
            # Version 1 has 4-byte item IDs; flag 1 makes each property index 2 bytes.
            id_width, index_width = (2 if data[part.content] == 0 else 4), (2 if data[part.content + 3] & 1 else 1)
            flag = 1 << (index_width * 8 - 1)  # marks a property as essential
            entries = []
            for ident in next(idents for found, idents in layout.association_boxes if found == part):
                if ident in live:
                    indices = layout.associations[ident]
                    entries.append(ident.to_bytes(id_width, "big") + bytes([len(indices)]) + b"".join(
                        (index | (flag if index in layout.essential[ident] else 0) if index in kept else 0)
                        .to_bytes(index_width, "big") for index in indices))
            parts.append(bmff.box(b"ipma", data[part.content:part.content + 4]
                                  + len(entries).to_bytes(4, "big") + b"".join(entries)))
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
    from a meta box's children."""
    for found in bmff.boxes(data, start, end):
        if found.kind == b"colr" and data[found.content:found.content + 4] in (b"prof", b"rICC"):
            yield found.content + 4, found.end
        elif found.kind in (b"iprp", b"ipco"):
            yield from profiles(data, found.content, found.end)


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


def clean_boxes(buffer, start, end):
    """Empty user data, metadata and uuid boxes, and clear times, names and
    data reference locations, in the boxes from start to end and below."""
    for found in bmff.boxes(buffer, start, end):
        if found.kind in EMPTIED:
            empty(buffer, found)
        elif found.kind in CONTAINERS:
            clean_boxes(buffer, found.content, found.end)
        else:
            movie.clear(buffer, movie.cleared_fields(buffer, found))


def check_boxes(data, start, end):
    for found in bmff.boxes(data, start, end):
        if found.kind in EMPTIED - {b"free"}:
            fail("user data or metadata box kept")
        if not zeroed(data, movie.cleared_fields(data, found)) or (found.kind == b"free"
                                                                   and not zeroed(data, [(found.content, found.end)])):
            fail("names or times kept")
        if found.kind in CONTAINERS:
            check_boxes(data, found.content, found.end)


def unused_media(data):
    """Spans of mdat and idat that no remaining item or track sample uses."""
    top, layout = top_boxes(data), bmff.layout(data)
    used = [span for spans in layout.extents.values() for span in spans] if layout else []
    for found in top:
        if found.kind == b"moov":
            used += movie.movie_ranges(data, found)
    used.sort()
    return [gap for start, end in media_spans(top, layout) for gap in gaps(used, start, end)]


def media_spans(top, layout):
    """Content spans of the boxes holding item and sample data: mdat and idat."""
    spans = [(found.content, found.end) for found in top if found.kind == b"mdat"]
    return spans + [layout.idat] if layout and layout.idat else spans


def listed(kinds):
    return ", ".join(sorted(kind.decode("latin-1") for kind in kinds))


# ---------------------------------------------------------------- verification

def check(original, rebuilt):
    top = top_boxes(rebuilt)
    kept = kept_boxes(top)
    for found in top:
        if found not in kept and not (found.kind == b"free" and zeroed(rebuilt, [(found.content, found.end)])):
            fail("unexpected box %r" % found.kind)
    if not movie.matches(original, rebuilt, top[0], file_type_brands(original, top[0])):
        fail("file type box changed")
    before, after = bmff.layout(original), bmff.layout(rebuilt)
    if (before is None) != (after is None) or (after and after.primary != before.primary):
        fail("primary image changed")
    if after:
        check_cleaned_items(original, before, rebuilt, after)
    if not zeroed(rebuilt, unused_media(rebuilt)):
        fail("unused media data kept")
    for found in top:
        if found.kind == b"meta":
            check_boxes(rebuilt, children(found), found.end)
        if found.kind == b"moov":
            # The movie box never moves: it is checked against the original's, box by box.
            movie.check(original, rebuilt, found, SEQUENCE)
            if not movie.same(original, rebuilt, movie.movie_ranges(rebuilt, found)):
                fail("sequence samples changed")


def check_cleaned_items(original, before, rebuilt, after):
    for child in after.children:
        if child.kind not in META_KEPT and not (child.kind == b"free" and zeroed(rebuilt, [(child.content, child.end)])):
            fail("unexpected item box %r" % child.kind)
    if not set(after.items) <= set(before.items):
        fail("unexpected item")
    check_tone_maps(rebuilt, after)
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
        for index in after.associations.get(ident, []):
            prop = after.props.get(index)
            if prop and prop.kind not in DISPLAY_PROPERTIES:
                fail("item property %r kept" % prop.kind)
            if prop:
                check_size(rebuilt, prop)
        # Item tables may be compacted, so item profiles are matched by item.
        if item_profiles(rebuilt, after, ident) != [icc.sanitize(profile)
                                                    for profile in item_profiles(original, before, ident)]:
            fail("color profile of item %d not sanitized" % ident)


def fail(detail):
    raise VerificationError("verification_failed", detail=detail)
