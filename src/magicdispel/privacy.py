"""Sanitize profile identity data and opaque HEIF metadata without recompression."""

import struct

from . import xmp


class PrivacyError(ValueError):
    pass


ICC_DATE = struct.pack('>6H', 2000, 1, 1, 0, 0, 0)
ICC_MAX_SIZE = 64 * 1024 * 1024
# Tags that describe where a profile came from rather than how to convert
# colors. They are dropped; desc and cprt are rewritten with neutral text.
ICC_DROPPED_TAGS = {
    # Descriptions, device names, calibration dates and dictionaries.
    b'desc', b'cprt', b'dmnd', b'dmdd', b'dscm', b'vued', b'calt', b'targ',
    b'meta', b'pseq', b'psid', b'mmod', b'devs', b'scrd', b'crdi',
    # Display setup: native panel data, video-card gamma (per-display
    # calibration) and its parameters. An embedded image profile is only used
    # for the PCS transform, which relies on the XYZ/TRC/para tags kept below.
    b'ndin', b'vcgt', b'vcgp',
}
ICC_COLOR_TYPES = {
    b'rXYZ': {b'XYZ '}, b'gXYZ': {b'XYZ '}, b'bXYZ': {b'XYZ '},
    b'wtpt': {b'XYZ '}, b'bkpt': {b'XYZ '}, b'lumi': {b'XYZ '},
    b'rTRC': {b'curv', b'para'}, b'gTRC': {b'curv', b'para'},
    b'bTRC': {b'curv', b'para'}, b'kTRC': {b'curv', b'para'},
    b'chad': {b'sf32'}, b'chrm': {b'chrm'}, b'cicp': {b'cicp'},
    b'view': {b'view'}, b'meas': {b'meas'}, b'tech': {b'sig '},
    b'gamt': {b'mft1', b'mft2', b'mAB ', b'mBA '},
    b'rig0': {b'sig '}, b'rig2': {b'sig '}, b'ciis': {b'sig '},
    b'hdgm': {b'gmap'},
    # Apple's per-channel parametric curves in macOS display profiles.
    b'aarg': {b'para'}, b'aagg': {b'para'}, b'aabg': {b'para'},
}
for _n in range(3):
    ICC_COLOR_TYPES[('A2B%d' % _n).encode()] = {b'mft1', b'mft2', b'mAB '}
    ICC_COLOR_TYPES[('B2A%d' % _n).encode()] = {b'mft1', b'mft2', b'mBA '}
    ICC_COLOR_TYPES[('pre%d' % _n).encode()] = {b'mft1', b'mft2', b'mAB ', b'mBA '}
for _n in range(4):
    ICC_COLOR_TYPES[('D2B%d' % _n).encode()] = {b'mpet'}
    ICC_COLOR_TYPES[('B2D%d' % _n).encode()] = {b'mpet'}


def icc_entries(profile):
    if (not 132 <= len(profile) <= ICC_MAX_SIZE or profile[36:40] != b'acsp'
            or profile[8] not in {2, 4}):
        raise PrivacyError('Unsupported or invalid ICC profile')
    length, = struct.unpack_from('>I', profile)
    count, = struct.unpack_from('>I', profile, 128)
    if count > 4096 or not 132 + 12 * count <= length <= len(profile):
        raise PrivacyError('Invalid ICC profile table')
    entries = {}
    ranges = []
    for n in range(count):
        tag, offset, size = struct.unpack_from('>4sII', profile, 132 + 12 * n)
        if (tag in entries or offset < 132 + 12 * count or size < 8
                or offset % 4 or offset + size > length):
            raise PrivacyError('Invalid ICC tag range')
        for a, b in ranges:
            if offset < b and offset + size > a and (offset, offset + size) != (a, b):
                raise PrivacyError('Overlapping ICC tag data')
        ranges.append((offset, offset + size))
        entries[tag] = profile[offset:offset + size]
    return entries


def sanitize_adaptive_curve(value):
    # Apple's legacy gmap includes an image-specific 16-byte identifier. Limit
    # this rewrite to the verified layout; never guess offsets in another type.
    if (len(value) < 158 or value[:12] != b'gmap' + b'\0' * 8
            or struct.unpack_from('>I', value, 12)[0] != len(value)
            or struct.unpack_from('>5I', value, 16) != (98, 106, 106, 106, 0)
            or value[60:64] != b'A2B0' or any(value[64:96])
            or value[98:106] != b'\x01\x00\x08\x0c\0\0\0\0'):
        raise PrivacyError('Unsupported HDR adaptive curve metadata layout')
    for offset in (36, 44, 52):
        start, length = struct.unpack_from('>II', value, offset)
        if start < 150 or length < 8 or start + length > len(value):
            raise PrivacyError('Invalid HDR adaptive curve data range')
    return value[:106] + b'\0' * 16 + value[122:]


def validate_apple_parametric_curve(value):
    """Apple screenshot curves use the standard ICC parametricCurveType layout."""
    if (len(value) < 12 or value[:8] != b'para' + b'\0' * 4
            or value[10:12] != b'\0\0'):
        raise PrivacyError('Invalid Apple ICC parametric curve')
    function, = struct.unpack_from('>H', value, 8)
    parameters = {0: 1, 1: 3, 2: 4, 3: 5, 4: 7}
    if function not in parameters or len(value) != 12 + 4 * parameters[function]:
        raise PrivacyError('Unsupported Apple ICC parametric curve layout')
    return value


def icc_color_signature(profile):
    entries = icc_entries(profile)
    kept = {}
    for tag, value in entries.items():
        if tag in ICC_DROPPED_TAGS:
            continue
        if tag not in ICC_COLOR_TYPES or value[:4] not in ICC_COLOR_TYPES[tag]:
            raise PrivacyError('Unsupported ICC color tag: ' + repr(tag))
        if tag in {b'aarg', b'aagg', b'aabg'}:
            validate_apple_parametric_curve(value)
        kept[tag] = sanitize_adaptive_curve(value) if tag == b'hdgm' else value
    return (profile[8:24], profile[44:48], profile[56:80], kept)


def icc_text(value, version, description=False):
    if version == 4:
        encoded = value.encode('utf-16be')
        return (b'mluc' + b'\0' * 4 + struct.pack('>II', 1, 12)
                + b'enUS' + struct.pack('>II', len(encoded), 28) + encoded)
    encoded = value.encode('ascii') + b'\0'
    if description:
        # ASCII description, empty Unicode and Macintosh descriptions.
        return b'desc' + b'\0' * 4 + struct.pack('>I', len(encoded)) + encoded + b'\0' * 78
    return b'text' + b'\0' * 4 + encoded


def sanitize_icc(profile):
    """Rebuild at the same byte length; discarded text and dead padding are zeroed."""
    _, _, _, color = icc_color_signature(profile)
    tags = {b'desc': icc_text('Clean', profile[8], True),
            b'cprt': icc_text('', profile[8])}
    tags.update(color)
    clean = bytearray(len(profile))
    clean[8:24] = profile[8:24]
    clean[24:36] = ICC_DATE
    clean[36:40] = b'acsp'
    clean[44:48] = profile[44:48]
    clean[56:80] = profile[56:80]
    # CMM, platform, maker/model, creator, old profile ID, and reserved bytes
    # are unspecified (zero). No new current-time or machine identity is added.
    struct.pack_into('>I', clean, 0, len(clean))
    struct.pack_into('>I', clean, 128, len(tags))
    offset = 132 + 12 * len(tags)
    dedup = {}
    for n, (tag, payload) in enumerate(tags.items()):
        if payload not in dedup:
            if offset + len(payload) > len(clean):
                raise PrivacyError('ICC profile has insufficient space for sanitized metadata')
            dedup[payload] = offset
            clean[offset:offset + len(payload)] = payload
            offset += (len(payload) + 3) & ~3
        struct.pack_into('>4sII', clean, 132 + 12 * n, tag, dedup[payload], len(payload))
    result = bytes(clean)
    if icc_color_signature(profile) != icc_color_signature(result):
        raise PrivacyError('ICC color conversion data changed')
    return result


def bmff_boxes(data, start=0, end=None):
    end = len(data) if end is None else end
    while start < end:
        if start + 8 > end:
            raise PrivacyError('Truncated image container')
        size, kind = struct.unpack_from('>I4s', data, start)
        header = 8
        if size == 1:
            if start + 16 > end:
                raise PrivacyError('Truncated extended image box')
            size, = struct.unpack_from('>Q', data, start + 8)
            header = 16
        elif size == 0:
            size = end - start
        if size < header or start + size > end:
            raise PrivacyError('Invalid image box length')
        yield kind, start, start + header, start + size
        start += size


def bmff_box(kind, payload):
    return struct.pack('>I4s', len(payload) + 8, kind) + payload


def _iloc(data, content, end, idat, empty_metadata=()):
    """Read item extents and retain each serialized entry for a safe rebuild."""
    if end - content < 8:
        raise PrivacyError('Truncated HEIF item locations')
    version = data[content]
    if version not in {0, 1, 2}:
        raise PrivacyError('Unsupported HEIF item locations')
    p = content + 4
    offset_size, length_size = data[p] >> 4, data[p] & 15
    base_size = data[p + 1] >> 4
    index_size = (data[p + 1] & 15) if version else 0
    if any(n not in {0, 4, 8} for n in (offset_size, length_size, base_size, index_size)):
        raise PrivacyError('Unsupported HEIF offset size')
    p += 2
    id_size = 4 if version == 2 else 2

    def number(n):
        nonlocal p
        if p + n > end:
            raise PrivacyError('Truncated HEIF item location')
        value = int.from_bytes(data[p:p + n], 'big')
        p += n
        return value

    count = number(id_size)
    prefix = bytes(data[content:p - id_size])
    items = {}
    for _ in range(count):
        begin = p
        ident = number(id_size)
        method = number(2) if version else 0
        reference = number(2)
        base = number(base_size)
        extents = []
        for _ in range(number(2)):
            index = number(index_size)
            relative, size = number(offset_size), number(length_size)
            if method not in {0, 1} or reference or index:
                raise PrivacyError('Unsupported external or indirect HEIF data')
            absolute = base + relative
            if method == 1:
                if idat is None:
                    raise PrivacyError('Missing HEIF item data box')
                absolute += idat[0]
                limit = idat[1]
            else:
                limit = len(data)
            if (not size and ident not in empty_metadata) or absolute + size > limit:
                raise PrivacyError('Invalid HEIF data extent')
            extents.append((absolute, absolute + size))
        if ident in items:
            raise PrivacyError('Duplicate HEIF item location')
        items[ident] = (bytes(data[begin:p]), extents)
    if p != end:
        raise PrivacyError('Unexpected HEIF location data')
    return prefix, id_size, items


def strip_heif_private(data):
    """Remove metadata items (EXIF, URI, JUMBF, and MIME items other than XMP)
    and their bytes, keeping all image item offsets. HEIF orientation lives in
    irot/imir properties, not EXIF; XMP is reduced by strip_heif_auxiliary."""
    result = bytearray(data)
    top = list(bmff_boxes(data))
    for kind, meta_start, meta_content, meta_end in top:
        if kind != b'meta':
            continue
        children = list(bmff_boxes(data, meta_content + 4, meta_end))
        infos = [b for b in children if b[0] == b'iinf']
        locations = [b for b in children if b[0] == b'iloc']
        if not infos:
            continue
        if len(infos) != 1 or len(locations) != 1:
            raise PrivacyError('Ambiguous HEIF item tables')
        _, _, content, end = infos[0]
        count_size = 2 if data[content] == 0 else 4
        entries = list(bmff_boxes(data, content + 4 + count_size, end))
        if len(entries) != int.from_bytes(data[content + 4:content + 4 + count_size], 'big'):
            raise PrivacyError('Invalid HEIF item count')
        deleted, kept, empty_metadata = set(), [], set()
        for t, a, b, c in entries:
            if t != b'infe' or data[b] not in {2, 3}:
                raise PrivacyError('Unsupported HEIF item information')
            width = 2 if data[b] == 2 else 4
            ident = int.from_bytes(data[b + 4:b + 4 + width], 'big')
            item_type = data[b + 6 + width:b + 10 + width]
            xmp_item = item_type == b'mime' and b'application/rdf+xml\0' in data[b:c]
            if item_type in (b'uri ', b'Exif', b'jumb') or (item_type == b'mime' and not xmp_item):
                deleted.add(ident)
            else:
                kept.append(bytes(data[a:c]))
                if xmp_item:
                    # Other tools may leave an empty XMP item after deleting it.
                    empty_metadata.add(ident)
        if not deleted:
            continue
        for t, _, b, c in children:
            if t == b'pitm':
                width = 2 if data[b] == 0 else 4
                if int.from_bytes(data[b + 4:b + 4 + width], 'big') in deleted:
                    raise PrivacyError('Private metadata is the primary HEIF item')
            elif t == b'iprp':
                for sub, _, begin, finish in bmff_boxes(data, b, c):
                    if sub != b'ipma':
                        continue
                    width = 2 if data[begin] == 0 else 4
                    association_width = 2 if data[begin + 3] & 1 else 1
                    count = int.from_bytes(data[begin + 4:begin + 8], 'big')
                    p = begin + 8
                    for _ in range(count):
                        if p + width + 1 > finish:
                            raise PrivacyError('Truncated HEIF property associations')
                        ident = int.from_bytes(data[p:p + width], 'big')
                        if ident in deleted:
                            raise PrivacyError('Private HEIF item has image properties')
                        p += width + 1 + data[p + width] * association_width
                    if p != finish:
                        raise PrivacyError('Invalid HEIF property associations')
        idats = [(b, c) for t, _, b, c in children if t == b'idat']
        if len(idats) > 1:
            raise PrivacyError('Ambiguous HEIF item data')
        _, _, content, end = locations[0]
        prefix, width, items = _iloc(data, content, end, idats[0] if idats else None, empty_metadata)
        if not deleted <= items.keys():
            raise PrivacyError('Missing HEIF private item location')
        protected = [span for ident, (_, spans) in items.items() if ident not in deleted for span in spans]
        for ident in deleted:
            for a, b in items[ident][1]:
                if any(a < d and b > c for c, d in protected):
                    raise PrivacyError('Private HEIF item overlaps image data')
                allowed_ranges = [(c, d) for t, _, c, d in top if t == b'mdat'] + idats
                if not any(a >= c and b <= d for c, d in allowed_ranges):
                    raise PrivacyError('Private HEIF data is not in a supported data box')
                result[a:b] = b'\0' * (b - a)
        replacements = {}
        _, a, b, _ = infos[0]
        replacements[a] = bmff_box(b'iinf', data[b:b + 4] + len(kept).to_bytes(count_size, 'big') + b''.join(kept))
        _, a, _, _ = locations[0]
        live = [entry for ident, (entry, _) in items.items() if ident not in deleted]
        replacements[a] = bmff_box(b'iloc', prefix + len(live).to_bytes(width, 'big') + b''.join(live))
        for t, a, b, c in children:
            if t == b'iref':
                width = 2 if data[b] == 0 else 4
                refs = []
                for ref_type, _, begin, finish in bmff_boxes(data, b + 4, c):
                    origin = int.from_bytes(data[begin:begin + width], 'big')
                    count = int.from_bytes(data[begin + width:begin + width + 2], 'big')
                    if begin + width + 2 + width * count != finish:
                        raise PrivacyError('Invalid HEIF reference list')
                    if origin in deleted:
                        if ref_type != b'cdsc':
                            raise PrivacyError('Private HEIF item has an image dependency')
                        continue
                    targets = [int.from_bytes(data[p:p + width], 'big')
                               for p in range(begin + width + 2, finish, width)]
                    if any(target in deleted for target in targets):
                        raise PrivacyError('Image depends on private HEIF metadata')
                    refs.append(bmff_box(ref_type, data[begin:finish]))
                replacements[a] = bmff_box(t, data[b:b + 4] + b''.join(refs))
        # Preserve the position of idat, so construction-method-1 extents still
        # refer to the same bytes. Removed metadata boxes become zeroed free space.
        for _, a, _, c in children:
            if a not in replacements:
                continue
            replacement = replacements[a]
            padding = c - a - len(replacement)
            if padding == 0:
                result[a:c] = replacement
                continue
            if padding < 8:
                raise PrivacyError('Insufficient HEIF padding for safe metadata removal')
            result[a:c] = replacement + bmff_box(b'free', b'\0' * (padding - 8))
    return bytes(result)


HEIF_DISPLAY_AUX = {
    b'urn:mpeg:hevc:2015:auxid:1',
    b'urn:mpeg:mpegB:cicp:systems:auxiliary:alpha',
    b'urn:com:apple:photo:2020:aux:hdrgainmap',
}
HEIF_PRIVATE_AUX = {
    b'urn:mpeg:hevc:2015:auxid:2',
    b'urn:mpeg:mpegB:cicp:systems:auxiliary:depth',
    b'urn:com:apple:photo:2018:aux:portraiteffectsmatte',
    b'tag:apple.com,2023:photo:aux:linearthumbnail',
    b'tag:apple.com,2023:photo:aux:styledeltamap',
} | {('urn:com:apple:photo:%s:aux:semantic%smatte' % (year, name)).encode()
     for year, name in [('2019', 'skin'), ('2019', 'hair'), ('2019', 'teeth'),
                        ('2020', 'glasses'), ('2020', 'sky')]}


def heif_layout(data):
    """Parse item IDs, associations and references without decoding pixels."""
    top = list(bmff_boxes(data))
    metas = [box for box in top if box[0] == b'meta']
    if not metas:
        return None
    if len(metas) != 1:
        raise PrivacyError('Multiple HEIF metadata tables are unsupported')
    _, _, start, end = metas[0]
    if data[start:start + 4] != b'\0' * 4:
        raise PrivacyError('Unsupported HEIF metadata version')
    children = list(bmff_boxes(data, start + 4, end))

    def one(kind, optional=False):
        boxes = [box for box in children if box[0] == kind]
        if not boxes and optional:
            return None
        if len(boxes) != 1:
            raise PrivacyError('Ambiguous HEIF ' + repr(kind) + ' table')
        return boxes[0]

    info, location, primary_box = one(b'iinf'), one(b'iloc'), one(b'pitm')
    _, _, b, c = primary_box
    if c - b < 6 or data[b] not in {0, 1} or c - b != (6 if data[b] == 0 else 8):
        raise PrivacyError('Invalid HEIF primary item')
    primary = int.from_bytes(data[b + 4:c], 'big')
    _, _, b, c = info
    if c - b < 6 or data[b] not in {0, 1}:
        raise PrivacyError('Unsupported HEIF item table')
    count_size = 2 if data[b] == 0 else 4
    records = list(bmff_boxes(data, b + 4 + count_size, c))
    if len(records) != int.from_bytes(data[b + 4:b + 4 + count_size], 'big'):
        raise PrivacyError('Invalid HEIF item count')
    items = {}
    for tag, a, b, c in records:
        if tag != b'infe' or c - b < 13 or data[b] not in {2, 3}:
            raise PrivacyError('Unsupported HEIF item information')
        width = 2 if data[b] == 2 else 4
        if b + 10 + width >= c or any(data[b + 4 + width:b + 6 + width]):
            raise PrivacyError('Truncated or encrypted HEIF item')
        ident = int.from_bytes(data[b + 4:b + 4 + width], 'big')
        kind = data[b + 6 + width:b + 10 + width]
        name = b + 10 + width
        terminator = data.find(b'\0', name, c)
        if ident in items or terminator < 0:
            raise PrivacyError('Duplicate or malformed HEIF item')
        items[ident] = {'type': kind, 'box': (tag, a, b, c), 'name': (name, terminator),
                        'xmp': kind == b'mime' and data[terminator + 1:c].startswith(b'application/rdf+xml\0')}
    if primary not in items:
        raise PrivacyError('Missing primary image item')
    idat_box = one(b'idat', True)
    idat = idat_box[2:] if idat_box else None
    _, _, b, c = location
    prefix, width, extents = _iloc(data, b, c, idat, {i for i, v in items.items() if v['xmp']})
    if items.keys() != extents.keys():
        raise PrivacyError('HEIF item information and locations differ')
    refs = []
    reference_box = one(b'iref', True)
    if reference_box:
        _, _, b, c = reference_box
        if c - b < 4 or data[b] not in {0, 1}:
            raise PrivacyError('Unsupported HEIF references')
        rw = 2 if data[b] == 0 else 4
        for kind, _, begin, finish in bmff_boxes(data, b + 4, c):
            if begin + rw + 2 > finish:
                raise PrivacyError('Truncated HEIF reference')
            origin = int.from_bytes(data[begin:begin + rw], 'big')
            count = int.from_bytes(data[begin + rw:begin + rw + 2], 'big')
            if begin + rw + 2 + count * rw != finish:
                raise PrivacyError('Invalid HEIF reference count')
            targets = [int.from_bytes(data[p:p + rw], 'big') for p in range(begin + rw + 2, finish, rw)]
            if origin not in items or any(t not in items for t in targets):
                raise PrivacyError('Dangling HEIF item reference')
            refs.append((kind, origin, targets))
    prop_box = one(b'iprp', True)
    props, associations, association_boxes = {}, {}, []
    if prop_box:
        pc = list(bmff_boxes(data, prop_box[2], prop_box[3]))
        containers = [x for x in pc if x[0] == b'ipco']
        if len(containers) != 1 or any(x[0] not in {b'ipco', b'ipma', b'free'} for x in pc):
            raise PrivacyError('Unsupported HEIF property container')
        props = dict(enumerate(bmff_boxes(data, containers[0][2], containers[0][3]), 1))
        for tag, a, b, c in pc:
            if tag != b'ipma':
                continue
            if c - b < 8 or data[b] not in {0, 1} or int.from_bytes(data[b + 1:b + 4], 'big') & ~1:
                raise PrivacyError('Unsupported HEIF property association')
            iw, aw = (2 if data[b] == 0 else 4), (2 if data[b + 3] & 1 else 1)
            p, entries = b + 8, []
            for _ in range(int.from_bytes(data[b + 4:b + 8], 'big')):
                begin = p
                if p + iw + 1 > c:
                    raise PrivacyError('Truncated HEIF association')
                ident = int.from_bytes(data[p:p + iw], 'big')
                count = data[p + iw]
                p += iw + 1
                if ident not in items or p + count * aw > c or ident in associations:
                    raise PrivacyError('Invalid HEIF association')
                values = [int.from_bytes(data[q:q + aw], 'big') & ((1 << (aw * 8 - 1)) - 1)
                          for q in range(p, p + count * aw, aw)]
                if any(n and n not in props for n in values):
                    raise PrivacyError('Missing HEIF property')
                p += count * aw
                associations[ident] = values
                entries.append((ident, bytes(data[begin:p])))
            if p != c:
                raise PrivacyError('Unexpected HEIF association bytes')
            association_boxes.append(((tag, a, b, c), entries))
    return dict(top=top, meta=metas[0], children=children, info=info, count_size=count_size,
                location=location, location_prefix=prefix, location_width=width,
                items=items, extents=extents, primary=primary, idat=idat,
                references=refs, reference_box=reference_box, props=props,
                associations=associations, association_boxes=association_boxes, prop_box=prop_box)


def heif_auxiliary_types(data, layout):
    result = {}
    for ident, indices in layout['associations'].items():
        types = []
        for index in indices:
            if not index:
                continue
            kind, _, b, c = layout['props'][index]
            if kind == b'auxC':
                zero = data.find(b'\0', b + 4, c)
                if data[b:b + 4] != b'\0' * 4 or zero < 0:
                    raise PrivacyError('Malformed HEIF auxiliary type')
                types.append(data[b + 4:zero])
        if len(types) > 1:
            raise PrivacyError('Ambiguous HEIF auxiliary image')
        if types:
            result[ident] = types[0]
    return result


def _hdr_only_xmp(raw):
    """An XMP item reduced to its recognized HDR fields and padded with spaces
    to its old length, so no offsets move; None if no field remains."""
    if len(raw) > 16 * 1024 * 1024:
        raise PrivacyError('Unsupported XMP packet')
    try:
        packet = xmp.hdr_packet(xmp.hdr_fields(raw))
    except xmp.XMPError as error:
        raise PrivacyError('Invalid HEIF XMP packet') from error
    if not packet:
        return None
    if len(packet) > len(raw):
        raise PrivacyError('XMP cleanup exceeds its allocated data range')
    return packet + b' ' * (len(raw) - len(packet))


def strip_heif_auxiliary(data):
    """Drop editing-only images, thumbnails and their metadata; retain HDR/alpha.

    Shared image tiles are protected. Removed payloads and unused properties are
    zeroed, and idat/mdat offsets stay stable. Unknown dependencies fail closed.
    """
    layout = heif_layout(data)
    if layout is None:
        return data
    items, refs = layout['items'], layout['references']
    auxiliary = heif_auxiliary_types(data, layout)
    unknown = set(auxiliary.values()) - HEIF_DISPLAY_AUX - HEIF_PRIVATE_AUX
    if unknown:
        raise PrivacyError('Unsupported HEIF auxiliary image: ' + repr(sorted(unknown)))
    seeds = {ident for ident, kind in auxiliary.items() if kind in HEIF_PRIVATE_AUX}
    seeds.update(origin for kind, origin, _ in refs if kind == b'thmb')
    # XMP items keep only HDR fields; one left with none is removed entirely. A
    # packet that cannot be read only matters if its item would be kept.
    reduced_xmp = {}
    for ident, item in items.items():
        if item['xmp']:
            try:
                reduced_xmp[ident] = _hdr_only_xmp(b''.join(data[a:b] for a, b in layout['extents'][ident][1]))
            except PrivacyError as error:
                reduced_xmp[ident] = error
    seeds.update(i for i, packet in reduced_xmp.items() if packet is None)

    def dependencies(roots):
        found = set(roots)
        while True:
            expanded = found | {target for kind, origin, targets in refs if kind == b'dimg' and origin in found for target in targets}
            if expanded == found:
                return found
            found = expanded

    candidates = dependencies(seeds)
    protected = dependencies(set(items) - candidates)
    if layout['primary'] in candidates or seeds & protected:
        raise PrivacyError('An image needed for display depends on private auxiliary data')
    deleted = candidates - protected
    for kind, origin, targets in refs:
        if kind == b'cdsc' and targets and set(targets) <= deleted:
            if items[origin]['type'] not in {b'mime', b'Exif', b'uri '}:
                raise PrivacyError('Unsupported HEIF auxiliary descriptor')
            deleted.add(origin)
    live = set(items) - deleted
    result = bytearray(data)
    protected_spans = [span for i in live for span in layout['extents'][i][1]]
    allowed = [(b, c) for tag, _, b, c in layout['top'] if tag == b'mdat']
    if layout['idat']:
        allowed.append(layout['idat'])
    for ident in deleted:
        for a, b in layout['extents'][ident][1]:
            if a == b:
                continue
            if any(a < d and b > c for c, d in protected_spans):
                raise PrivacyError('Auxiliary image overlaps retained image data')
            if not any(a >= c and b <= d for c, d in allowed):
                raise PrivacyError('Auxiliary data is outside image data boxes')
            result[a:b] = b'\0' * (b - a)
    for ident in live:
        if not items[ident]['xmp']:
            continue
        spans = layout['extents'][ident][1]
        original = b''.join(data[a:b] for a, b in spans)
        cleaned = reduced_xmp[ident]
        if isinstance(cleaned, PrivacyError):
            raise cleaned
        if cleaned != original:
            for a, b in spans:
                if any(other != ident and a < d and b > c for other in live
                       for c, d in layout['extents'][other][1]):
                    raise PrivacyError('XMP overlaps image data')
            p = 0
            for a, b in spans:
                result[a:b] = cleaned[p:p + b - a]
                p += b - a
    replacements = {}
    entries = []
    for ident, item in items.items():
        if ident in deleted:
            continue
        _, _, b, c = item['box']
        name, zero = item['name']
        # Keep the allocated name length, replacing its contents with blanks.
        # This avoids creating an unrepresentable 1..7-byte free-box remainder.
        entries.append(bmff_box(b'infe', data[b:name] + b' ' * (zero - name) + data[zero:c]))
    _, a, b, _ = layout['info']
    replacements[a] = bmff_box(b'iinf', data[b:b + 4] + len(entries).to_bytes(layout['count_size'], 'big') + b''.join(entries))
    _, a, _, _ = layout['location']
    entries = [raw for i, (raw, _) in layout['extents'].items() if i in live]
    replacements[a] = bmff_box(b'iloc', layout['location_prefix'] + len(entries).to_bytes(layout['location_width'], 'big') + b''.join(entries))
    if layout['reference_box']:
        _, a, b, _ = layout['reference_box']
        width = 2 if data[b] == 0 else 4
        entries = []
        for kind, origin, targets in refs:
            if origin in deleted or any(t in deleted for t in targets):
                if kind not in {b'dimg', b'cdsc', b'auxl', b'thmb'}:
                    raise PrivacyError('Unknown HEIF dependency on removed auxiliary data')
                if origin in deleted:
                    continue
                if kind == b'dimg':
                    raise PrivacyError('A retained image lost a dependency')
                targets = [t for t in targets if t in live]
                if not targets:
                    continue
            payload = origin.to_bytes(width, 'big') + len(targets).to_bytes(2, 'big') + b''.join(t.to_bytes(width, 'big') for t in targets)
            entries.append(bmff_box(kind, payload))
        replacements[a] = bmff_box(b'iref', data[b:b + 4] + b''.join(entries))
    if layout['prop_box']:
        used = {index for ident in live for index in layout['associations'].get(ident, [])}
        prop_parts = []
        for kind, a, b, c in bmff_boxes(data, layout['prop_box'][2], layout['prop_box'][3]):
            if kind == b'ipco':
                entries = [bytes(data[x:y]) if index in used else bmff_box(b'free', b'\0' * (y - x - 8))
                           for index, (_, x, _, y) in layout['props'].items()]
                prop_parts.append(bmff_box(kind, b''.join(entries)))
            elif kind == b'ipma':
                entries = next(entries for box, entries in layout['association_boxes'] if box[1] == a)
                kept = [entry for ident, entry in entries if ident in live]
                prop_parts.append(bmff_box(kind, data[b:b + 4] + len(kept).to_bytes(4, 'big') + b''.join(kept)))
            else:
                prop_parts.append(bmff_box(b'free', b'\0' * (c - a - 8)))
        replacements[layout['prop_box'][1]] = bmff_box(b'iprp', b''.join(prop_parts))
    for kind, a, b, c in layout['children']:
        if kind == b'grpl':
            for tag, _, start, end in bmff_boxes(data, b, c):
                if tag != b'altr' or data[start:start + 4] != b'\0' * 4 or end - start < 12:
                    raise PrivacyError('Unsupported HEIF image group')
                count = int.from_bytes(data[start + 8:start + 12], 'big')
                if start + 12 + 4 * count != end:
                    raise PrivacyError('Invalid HEIF image group')
                if any(int.from_bytes(data[p:p + 4], 'big') in deleted for p in range(start + 12, end, 4)):
                    raise PrivacyError('Removed auxiliary image is an alternate display image')
        if kind == b'hdlr' and c - b >= 24:
            replacements[a] = bmff_box(kind, data[b:b + 24] + b'\0' * (c - b - 24))
        if kind == b'free':
            replacements[a] = bmff_box(kind, b'\0' * (c - a - 8))
    # Compact the metadata tables between anchored idat boxes. This also handles
    # a removed ipma entry shorter than the minimum eight-byte free box.
    begin, parts = layout['meta'][2] + 4, []

    def flush(end):
        payload = b''.join(parts)
        gap = end - begin - len(payload)
        if gap < 0 or 0 < gap < 8:
            raise PrivacyError('Insufficient space for safe HEIF table rewrite')
        if gap:
            payload += bmff_box(b'free', b'\0' * (gap - 8))
        result[begin:end] = payload

    for kind, a, _, c in layout['children']:
        if kind == b'idat':
            flush(a)
            begin, parts = c, []
        else:
            parts.append(replacements.get(a, bytes(result[a:c])))
    flush(layout['meta'][3])
    cleaned = bytes(result)
    after = heif_layout(cleaned)
    if set(after['items']) != live or after['primary'] != layout['primary']:
        raise PrivacyError('HEIF item rewrite verification failed')
    for ident in live:
        if items[ident]['type'] in {b'Exif', b'mime', b'uri '}:
            continue
        for a, b in layout['extents'][ident][1]:
            if data[a:b] != cleaned[a:b]:
                raise PrivacyError('Retained HEIF image bytes changed')
    return cleaned


def sanitize_bmff_profiles(data):
    result = bytearray(data)
    count = 0

    def walk(start, end):
        nonlocal count
        for kind, a, b, c in bmff_boxes(data, start, end):
            if kind == b'colr' and data[b:b + 4] in {b'prof', b'rICC'}:
                result[b + 4:c] = sanitize_icc(bytes(data[b + 4:c]))
                count += 1
            elif kind in {b'meta', b'iprp', b'ipco', b'moov', b'trak', b'mdia', b'minf', b'stbl'}:
                walk(b + (4 if kind == b'meta' else 0), c)
            elif kind == b'stsd':
                for sample, _, begin, finish in bmff_boxes(data, b + 8, c):
                    if sample in {b'av01', b'hvc1', b'hev1', b'avc1'}:
                        walk(begin + 78, finish)
    walk(0, len(data))
    return bytes(result), count


