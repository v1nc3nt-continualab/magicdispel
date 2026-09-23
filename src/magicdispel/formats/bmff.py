"""Reading ISO base media files (HEIF, AVIF): boxes, and the item tables of the meta box.

HEIF stores each image, tile, auxiliary image (alpha, depth, gain map...) and
metadata block as an *item*. The meta box lists them (iinf), says where their
bytes are (iloc), how they relate (iref: derived images, auxiliaries,
descriptions, thumbnails), and which properties each has (iprp: ipco holds
the properties, ipma associates them with items). `layout` reads all of that
without decoding any pixels, and checks it is consistent.
"""
import struct
from dataclasses import dataclass
from typing import NamedTuple

XMP_TYPE = b"application/rdf+xml\0"


class StructureError(ValueError):
    """A structure that cannot be read safely: damaged, or merely unsupported."""

    def __init__(self, detail, damaged=True):
        super().__init__(detail)
        self.damaged = damaged


def unsupported(detail):
    return StructureError(detail, damaged=False)


class Box(NamedTuple):
    kind: bytes
    start: int    # the box header
    content: int  # right after the header
    end: int


@dataclass
class Item:
    kind: bytes   # item type: hvc1, av01, grid, tmap, Exif, mime...
    box: Box      # its infe entry
    name: tuple   # (start, end) of its name, without the terminator
    xmp: bool     # a MIME item holding XMP


@dataclass
class Layout:
    top: list                 # top-level boxes
    meta: Box
    children: list            # boxes inside meta
    info: Box                 # iinf
    count_size: int           # bytes of iinf's entry count
    location: Box             # iloc
    location_prefix: bytes    # iloc's version, flags and field sizes
    location_width: int       # bytes of iloc's item count and IDs
    items: dict               # {id: Item}
    location_entries: dict    # {id: serialized iloc entry}
    extents: dict             # {id: [(start, end), ...]}, absolute file ranges
    primary: int
    idat: tuple               # (content, end) of the item data box, or None
    references: list          # [(kind, origin, [targets])]
    reference_box: Box        # iref, or None
    props: dict               # {index: Box}, numbered from 1
    associations: dict        # {id: [property indices]}
    association_boxes: list   # [(ipma Box, [(id, serialized entry)])]
    prop_box: Box             # iprp, or None


def boxes(data, start=0, end=None):
    """The boxes between start and end, in order."""
    end = len(data) if end is None else end
    while start < end:
        if start + 8 > end:
            raise StructureError("truncated box")
        size, kind = struct.unpack_from(">I4s", data, start)
        header = 8
        if size == 1:  # a 64-bit size follows the type
            if start + 16 > end:
                raise StructureError("truncated extended box")
            size, = struct.unpack_from(">Q", data, start + 8)
            header = 16
        elif size == 0:  # the box extends to the end
            size = end - start
        if size < header or start + size > end:
            raise StructureError("invalid box length")
        yield Box(kind, start, start + header, start + size)
        start += size


def box(kind, payload):
    return struct.pack(">I4s", len(payload) + 8, kind) + payload


def layout(data):
    """The item tables of the file's meta box, or None if it has none."""
    top = list(boxes(data))
    metas = [found for found in top if found.kind == b"meta"]
    if not metas:
        return None
    if len(metas) != 1:
        raise unsupported("multiple meta boxes")
    meta = metas[0]
    if data[meta.content:meta.content + 4] != bytes(4):
        raise unsupported("meta box version")
    children = list(boxes(data, meta.content + 4, meta.end))

    def one(kind, optional=False):
        found = [child for child in children if child.kind == kind]
        if not found and optional:
            return None
        if len(found) != 1:
            raise unsupported("ambiguous %s table" % kind.decode("latin-1"))
        return found[0]

    primary = read_primary(data, one(b"pitm"))
    info = one(b"iinf")
    items, count_size = read_items(data, info)
    if primary not in items:
        raise StructureError("missing primary image item")
    idat_box = one(b"idat", optional=True)
    idat = (idat_box.content, idat_box.end) if idat_box else None
    location = one(b"iloc")
    prefix, width, entries, extents = read_locations(data, location, idat,
                                                     {ident for ident, item in items.items() if item.xmp})
    if items.keys() != extents.keys():
        raise StructureError("item information and locations differ")
    reference_box = one(b"iref", optional=True)
    references = read_references(data, reference_box, items) if reference_box else []
    prop_box = one(b"iprp", optional=True)
    props, associations, association_boxes = read_properties(data, prop_box, items) if prop_box else ({}, {}, [])
    return Layout(top, meta, children, info, count_size, location, prefix, width, items, entries, extents,
                  primary, idat, references, reference_box, props, associations, association_boxes, prop_box)


def auxiliary_types(data, found):
    """{item id: auxiliary type URN} for items that are auxiliary images."""
    result = {}
    for ident, indices in found.associations.items():
        types = []
        for index in indices:
            if index and found.props[index].kind == b"auxC":
                prop = found.props[index]
                terminator = data.find(b"\0", prop.content + 4, prop.end)
                if data[prop.content:prop.content + 4] != bytes(4) or terminator < 0:
                    raise StructureError("malformed auxiliary type")
                types.append(bytes(data[prop.content + 4:terminator]))
        if len(types) > 1:
            raise unsupported("ambiguous auxiliary image")
        if types:
            result[ident] = types[0]
    return result


def read_primary(data, pitm):
    content, end = pitm.content, pitm.end
    if end - content < 6 or data[content] not in (0, 1) or end - content != (6 if data[content] == 0 else 8):
        raise StructureError("invalid primary item")
    return int.from_bytes(data[content + 4:end], "big")


def read_items(data, info):
    """{id: Item} from iinf, and the size of its entry count."""
    content, end = info.content, info.end
    if end - content < 6 or data[content] not in (0, 1):
        raise unsupported("item table version")
    count_size = 2 if data[content] == 0 else 4
    records = list(boxes(data, content + 4 + count_size, end))
    if len(records) != int.from_bytes(data[content + 4:content + 4 + count_size], "big"):
        raise StructureError("invalid item count")
    items = {}
    for record in records:
        body, stop = record.content, record.end
        if record.kind != b"infe" or stop - body < 13 or data[body] not in (2, 3):
            raise unsupported("item information version")
        width = 2 if data[body] == 2 else 4
        # After the item ID: protection index (2 bytes, 0 if unprotected), type, name.
        if body + 10 + width >= stop or any(data[body + 4 + width:body + 6 + width]):
            raise unsupported("truncated or protected item")
        ident = int.from_bytes(data[body + 4:body + 4 + width], "big")
        kind = bytes(data[body + 6 + width:body + 10 + width])
        name = body + 10 + width
        terminator = data.find(b"\0", name, stop)
        if ident in items or terminator < 0:
            raise StructureError("duplicate or malformed item")
        xmp = kind == b"mime" and data[terminator + 1:stop].startswith(XMP_TYPE)
        items[ident] = Item(kind, record, (name, terminator), xmp)
    return items, count_size


def read_locations(data, location, idat, empty_allowed=()):
    """iloc's prefix and ID size, {id: serialized entry} and {id: extents}.
    Extents are absolute file ranges; empty ones are allowed only for `empty_allowed`."""
    content, end = location.content, location.end
    if end - content < 8:
        raise StructureError("truncated item locations")
    version = data[content]
    if version not in (0, 1, 2):
        raise unsupported("item location version")
    p = content + 4
    offset_size, length_size = data[p] >> 4, data[p] & 15
    base_size = data[p + 1] >> 4
    index_size = (data[p + 1] & 15) if version else 0
    if any(size not in (0, 4, 8) for size in (offset_size, length_size, base_size, index_size)):
        raise unsupported("item location field size")
    p += 2
    id_size = 4 if version == 2 else 2

    def number(size):
        nonlocal p
        if p + size > end:
            raise StructureError("truncated item location")
        value = int.from_bytes(data[p:p + size], "big")
        p += size
        return value

    count = number(id_size)
    prefix = bytes(data[content:p - id_size])
    entries, found = {}, {}
    for _ in range(count):
        begin = p
        ident = number(id_size)
        method = number(2) if version else 0  # 0: file offsets, 1: offsets into idat
        reference = number(2)
        base = number(base_size)
        extents = []
        for _ in range(number(2)):
            index = number(index_size)
            relative, size = number(offset_size), number(length_size)
            if method not in (0, 1) or reference or index:
                raise unsupported("external or indirect item data")
            start = base + relative
            if method == 1:
                if idat is None:
                    raise StructureError("missing item data box")
                start += idat[0]
                limit = idat[1]
            else:
                limit = len(data)
            if (not size and ident not in empty_allowed) or start + size > limit:
                raise StructureError("invalid item data extent")
            extents.append((start, start + size))
        if ident in found:
            raise StructureError("duplicate item location")
        entries[ident], found[ident] = bytes(data[begin:p]), extents
    if p != end:
        raise StructureError("unexpected item location data")
    return prefix, id_size, entries, found


def read_references(data, iref, items):
    """[(kind, origin, [targets])] from iref; every ID must be a known item."""
    content, end = iref.content, iref.end
    if end - content < 4 or data[content] not in (0, 1):
        raise unsupported("item reference version")
    width = 2 if data[content] == 0 else 4
    references = []
    for reference in boxes(data, content + 4, end):
        body, stop = reference.content, reference.end
        if body + width + 2 > stop:
            raise StructureError("truncated item reference")
        origin = int.from_bytes(data[body:body + width], "big")
        count = int.from_bytes(data[body + width:body + width + 2], "big")
        if body + width + 2 + count * width != stop:
            raise StructureError("invalid item reference count")
        targets = [int.from_bytes(data[p:p + width], "big") for p in range(body + width + 2, stop, width)]
        if origin not in items or any(target not in items for target in targets):
            raise StructureError("dangling item reference")
        references.append((reference.kind, origin, targets))
    return references


def read_properties(data, iprp, items):
    """Properties {index: Box}, associations {id: [indices]} and the ipma
    entries as serialized, from iprp."""
    parts = list(boxes(data, iprp.content, iprp.end))
    containers = [part for part in parts if part.kind == b"ipco"]
    if len(containers) != 1 or any(part.kind not in (b"ipco", b"ipma", b"free") for part in parts):
        raise unsupported("item property container")
    props = dict(enumerate(boxes(data, containers[0].content, containers[0].end), 1))
    associations, association_boxes = {}, []
    for part in parts:
        if part.kind != b"ipma":
            continue
        content, end = part.content, part.end
        if end - content < 8 or data[content] not in (0, 1) or int.from_bytes(data[content + 1:content + 4], "big") & ~1:
            raise unsupported("item property association version")
        # Version 1 has 4-byte item IDs; flag 1 makes each property index 2 bytes.
        id_width, index_width = (2 if data[content] == 0 else 4), (2 if data[content + 3] & 1 else 1)
        p, entries = content + 8, []
        for _ in range(int.from_bytes(data[content + 4:content + 8], "big")):
            begin = p
            if p + id_width + 1 > end:
                raise StructureError("truncated item property association")
            ident = int.from_bytes(data[p:p + id_width], "big")
            count = data[p + id_width]
            p += id_width + 1
            if ident not in items or p + count * index_width > end or ident in associations:
                raise StructureError("invalid item property association")
            # The top bit of each index marks the property as essential.
            values = [int.from_bytes(data[q:q + index_width], "big") & ((1 << (index_width * 8 - 1)) - 1)
                      for q in range(p, p + count * index_width, index_width)]
            if any(value and value not in props for value in values):
                raise StructureError("missing item property")
            p += count * index_width
            associations[ident] = values
            entries.append((ident, bytes(data[begin:p])))
        if p != end:
            raise StructureError("unexpected item property association data")
        association_boxes.append((part, entries))
    return props, associations, association_boxes
