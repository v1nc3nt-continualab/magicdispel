"""ICC color profiles: keep the color conversion data, drop where the profile came from.

`sanitize` rebuilds a profile at the same byte length with only its color
tags, a neutral description and copyright, a fixed date and no creator,
maker, model, CMM, platform or profile ID. It checks that the color data is
unchanged, and refuses tags it does not know rather than copying them. A kept
tag is copied as it is, so every byte of it must be part of its type's layout
(ICC.1 section 10), or zero: reserved fields, padding and unused space.
"""
import struct


class ProfileError(ValueError):
    """A profile that cannot be sanitized safely."""


MAX_SIZE = 64 * 1024 * 1024
# Header fields (ICC.1 section 7.2) copied into a sanitized profile. Everything
# else in the header - CMM, platform, maker, model, creator, profile ID - stays zero.
CLASS_AND_SPACES = slice(8, 24)    # version, device class, color space, connection space
DATE = slice(24, 36)
SIGNATURE = slice(36, 40)          # "acsp"
FLAGS = slice(44, 48)
RENDERING = slice(56, 80)          # device attributes, rendering intent, illuminant
TAG_COUNT, TAG_TABLE = 128, 132
PLACEHOLDER_DATE = struct.pack(">6H", 2000, 1, 1, 0, 0, 0)

# Tags that describe where a profile came from rather than how to convert
# colors. They are dropped; desc and cprt are rewritten with neutral text.
DROPPED_TAGS = {
    # Descriptions, device names, calibration dates and dictionaries.
    b"desc", b"cprt", b"dmnd", b"dmdd", b"dscm", b"vued", b"calt", b"targ",
    b"meta", b"pseq", b"psid", b"mmod", b"devs", b"scrd", b"crdi",
    # Display setup: native panel data, video-card gamma (per-display
    # calibration) and its parameters. An embedded image profile is only used
    # for the PCS transform, which relies on the XYZ/TRC/para tags kept below.
    b"ndin", b"vcgt", b"vcgp",
}
# Color tags that are kept, with the tag types each may have.
COLOR_TYPES = {
    b"rXYZ": {b"XYZ "}, b"gXYZ": {b"XYZ "}, b"bXYZ": {b"XYZ "},
    b"wtpt": {b"XYZ "}, b"bkpt": {b"XYZ "}, b"lumi": {b"XYZ "},
    b"rTRC": {b"curv", b"para"}, b"gTRC": {b"curv", b"para"},
    b"bTRC": {b"curv", b"para"}, b"kTRC": {b"curv", b"para"},
    b"chad": {b"sf32"}, b"chrm": {b"chrm"}, b"cicp": {b"cicp"},
    b"view": {b"view"}, b"meas": {b"meas"}, b"tech": {b"sig "},
    b"gamt": {b"mft1", b"mft2", b"mAB ", b"mBA "},
    b"rig0": {b"sig "}, b"rig2": {b"sig "}, b"ciis": {b"sig "},
    b"hdgm": {b"gmap"},
    # Apple's per-channel parametric curves in macOS display profiles.
    b"aarg": {b"para"}, b"aagg": {b"para"}, b"aabg": {b"para"},
}
for _n in range(3):
    COLOR_TYPES[b"A2B%d" % _n] = {b"mft1", b"mft2", b"mAB "}
    COLOR_TYPES[b"B2A%d" % _n] = {b"mft1", b"mft2", b"mBA "}
    COLOR_TYPES[b"pre%d" % _n] = {b"mft1", b"mft2", b"mAB ", b"mBA "}
# Floating-point transforms (D2Bx/B2Dx, multiProcessElementsType) are not
# accepted: their elements are not checked byte for byte.
APPLE_CURVES = {b"aarg", b"aagg", b"aabg"}
# parametricCurveType: number of parameters for each function type.
PARAMETRIC_FUNCTIONS = {0: 1, 1: 3, 2: 4, 3: 5, 4: 7}
# Types of a fixed size: the type signature, 4 reserved bytes, then data up to here.
FIXED_SIZES = {b"XYZ ": 20, b"sf32": 44, b"cicp": 12, b"sig ": 12, b"view": 36, b"meas": 36}
MATRIX = 48  # the 3x3 matrix and offsets of a lutAToB or lutBToA transform
# Apple's legacy HDR "gmap" curve stores the identifier of the image it came
# from here, then seven numbers, then from CURVE_DATA on the curve itself.
GMAP_IDENTIFIER = slice(106, 122)
CURVE_DATA = 150


def sanitize(profile):
    """The profile rebuilt at the same length; dropped text and dead space are zero."""
    color = color_signature(profile)[3]
    version = profile[8]
    tags = {b"desc": text_tag("Clean", version, description=True), b"cprt": text_tag("", version)}
    tags.update(color)
    clean = bytearray(len(profile))
    for field in (CLASS_AND_SPACES, FLAGS, RENDERING):
        clean[field] = profile[field]
    clean[DATE] = PLACEHOLDER_DATE
    clean[SIGNATURE] = b"acsp"
    struct.pack_into(">I", clean, 0, len(clean))
    struct.pack_into(">I", clean, TAG_COUNT, len(tags))
    offset, placed = TAG_TABLE + 12 * len(tags), {}
    for index, (tag, payload) in enumerate(tags.items()):
        if payload not in placed:  # identical tags share their data, as in the original
            if offset + len(payload) > len(clean):
                raise ProfileError("ICC profile has insufficient space for sanitized metadata")
            placed[payload] = offset
            clean[offset:offset + len(payload)] = payload
            offset += (len(payload) + 3) & ~3
        struct.pack_into(">4sII", clean, TAG_TABLE + 12 * index, tag, placed[payload], len(payload))
    result = bytes(clean)
    if color_signature(profile) != color_signature(result):
        raise ProfileError("ICC color conversion data changed")
    return result


def color_signature(profile):
    """Everything that determines color conversion: header fields and color tags."""
    kept = {}
    for tag, value in entries(profile).items():
        if tag in DROPPED_TAGS:
            continue
        if tag not in COLOR_TYPES or value[:4] not in COLOR_TYPES[tag]:
            raise ProfileError("Unsupported ICC color tag: " + repr(tag))
        if tag in APPLE_CURVES:
            validate_parametric_curve(value)
        if tag == b"hdgm":
            kept[tag] = sanitize_adaptive_curve(value)
        else:
            check_layout(value)
            kept[tag] = value
    return profile[CLASS_AND_SPACES], profile[FLAGS], profile[RENDERING], kept


def check_layout(value):
    """Refuse a color tag holding anything its type's layout does not: every
    byte outside the fields of its type must be zero."""
    try:
        spans = sorted(layout_spans(value))
    except (struct.error, IndexError):
        raise ProfileError("Truncated ICC %s tag" % value[:4].decode("latin-1"))
    position = 0
    for start, end in spans:
        if end > len(value):
            raise ProfileError("Truncated ICC %s tag" % value[:4].decode("latin-1"))
        if any(value[position:start]):
            break
        position = max(position, end)
    else:
        if not any(value[position:]):
            return
    raise ProfileError("ICC %s tag holds data outside its layout" % value[:4].decode("latin-1"))


def layout_spans(value):
    """The (start, end) byte ranges of a color tag's fields, by its type."""
    kind, head = value[:4], [(0, 4)]  # the type signature; 4 reserved bytes follow
    if kind in FIXED_SIZES:
        return head + [(8, FIXED_SIZES[kind])]
    if kind in (b"curv", b"para"):
        return curve_spans(value, 0)[0]
    if kind == b"chrm":  # channels, colorant type, then x and y for each channel
        return head + [(8, 12 + 8 * struct.unpack_from(">H", value, 8)[0])]
    if kind in (b"mft1", b"mft2"):
        inputs, outputs, grid = value[8], value[9], value[10]  # a padding byte follows
        points = grid ** inputs * outputs
        if kind == b"mft1":  # the matrix, then 256-entry input tables, the CLUT and output tables
            return head + [(8, 11), (12, 48 + 256 * inputs + points + 256 * outputs)]
        entries = struct.unpack_from(">HH", value, 48)  # input and output table entries, 16 bits each
        return head + [(8, 11), (12, 52 + 2 * (entries[0] * inputs + points + entries[1] * outputs))]
    if kind in (b"mAB ", b"mBA "):
        return transform_spans(value)
    raise ProfileError("Unsupported ICC tag type %r" % kind)


def transform_spans(value):
    """lutAToBType and lutBToAType: channel counts, then the offsets of up to
    five elements (B curves, matrix, M curves, CLUT, A curves)."""
    inputs, outputs = value[8], value[9]
    b_curves, a_curves = (outputs, inputs) if value[:4] == b"mAB " else (inputs, outputs)
    b, matrix, m, clut, a = struct.unpack_from(">5I", value, 12)
    spans = [(0, 4), (8, 10), (12, 32)]
    for offset, count in ((b, b_curves), (m, b_curves), (a, a_curves)):
        position = offset
        for _ in range(count if offset else 0):
            if position % 4:
                raise ProfileError("Misaligned ICC curve")
            curve, end = curve_spans(value, position)
            spans += curve
            position = (end + 3) & ~3  # each curve starts on a 4-byte boundary
    if matrix:
        spans.append((matrix, matrix + MATRIX))
    if clut:
        grid, precision = value[clut:clut + 16], value[clut + 16]
        if len(grid) < 16 or any(grid[inputs:]) or not all(grid[:inputs]) or precision not in (1, 2):
            raise ProfileError("Unsupported ICC color lookup table")
        points = outputs * precision
        for size in grid[:inputs]:
            points *= size
        spans += [(clut, clut + 17), (clut + 20, clut + 20 + points)]  # 3 padding bytes after the precision
    return spans


def curve_spans(value, start):
    """([(start, end)] of its fields, end) of a curv or para curve at `start`."""
    kind = value[start:start + 4]
    if kind == b"curv":  # a count, then that many 16-bit entries
        end = start + 12 + 2 * struct.unpack_from(">I", value, start + 8)[0]
        return [(start, start + 4), (start + 8, end)], end
    if kind == b"para":  # a function type, 2 reserved bytes, then its parameters
        function = struct.unpack_from(">H", value, start + 8)[0]
        if function not in PARAMETRIC_FUNCTIONS:
            raise ProfileError("Unsupported ICC parametric curve")
        end = start + 12 + 4 * PARAMETRIC_FUNCTIONS[function]
        return [(start, start + 4), (start + 8, start + 10), (start + 12, end)], end
    raise ProfileError("Unsupported ICC curve type %r" % kind)


def entries(profile):
    """{tag: data} from the tag table, after checking every range."""
    if (not TAG_TABLE <= len(profile) <= MAX_SIZE or profile[SIGNATURE] != b"acsp"
            or profile[8] not in (2, 4)):
        raise ProfileError("Unsupported or invalid ICC profile")
    length, = struct.unpack_from(">I", profile)
    count, = struct.unpack_from(">I", profile, TAG_COUNT)
    table_end = TAG_TABLE + 12 * count
    if count > 4096 or not table_end <= length <= len(profile):
        raise ProfileError("Invalid ICC profile table")
    found, ranges = {}, []
    for index in range(count):
        tag, offset, size = struct.unpack_from(">4sII", profile, TAG_TABLE + 12 * index)
        if tag in found or offset < table_end or size < 8 or offset % 4 or offset + size > length:
            raise ProfileError("Invalid ICC tag range")
        # Tags may share data, but not partially overlap.
        if any(offset < end and offset + size > start and (offset, offset + size) != (start, end)
               for start, end in ranges):
            raise ProfileError("Overlapping ICC tag data")
        ranges.append((offset, offset + size))
        found[tag] = profile[offset:offset + size]
    return found


def sanitize_adaptive_curve(value):
    """Clear the image identifier in Apple's legacy "gmap" HDR curve, keeping the
    curve. Only the layout this was verified on is accepted; no offsets are guessed."""
    if (len(value) < CURVE_DATA + 8 or value[:12] != b"gmap" + b"\0" * 8
            or struct.unpack_from(">I", value, 12)[0] != len(value)
            or struct.unpack_from(">5I", value, 16) != (98, 106, 106, 106, 0)
            or value[60:64] != b"A2B0" or any(value[64:98])
            or value[98:106] != b"\x01\x00\x08\x0c\0\0\0\0"):
        raise ProfileError("Unsupported HDR adaptive curve metadata layout")
    # The channels' curves, usually one shared by all three: each a zero
    # field, a point count and 12 bytes per point, together filling the rest.
    position = CURVE_DATA
    for start, length in sorted({struct.unpack_from(">II", value, offset) for offset in (36, 44, 52)}):
        if (start != position or start + 8 > len(value) or any(value[start:start + 4])
                or length != 8 + 12 * struct.unpack_from(">I", value, start + 4)[0]):
            raise ProfileError("Invalid HDR adaptive curve data range")
        position = start + length
    if position != len(value):
        raise ProfileError("Invalid HDR adaptive curve data range")
    return value[:GMAP_IDENTIFIER.start] + bytes(16) + value[GMAP_IDENTIFIER.stop:]


def validate_parametric_curve(value):
    """Apple's screenshot curves use the standard parametricCurveType layout."""
    if len(value) < 12 or value[:8] != b"para" + b"\0" * 4 or value[10:12] != b"\0\0":
        raise ProfileError("Invalid Apple ICC parametric curve")
    function, = struct.unpack_from(">H", value, 8)
    if function not in PARAMETRIC_FUNCTIONS or len(value) != 12 + 4 * PARAMETRIC_FUNCTIONS[function]:
        raise ProfileError("Unsupported Apple ICC parametric curve layout")
    return value


def text_tag(value, version, description=False):
    """A text tag in the form the profile's version expects."""
    if version == 4:
        encoded = value.encode("utf-16be")
        return (b"mluc" + b"\0" * 4 + struct.pack(">II", 1, 12)
                + b"enUS" + struct.pack(">II", len(encoded), 28) + encoded)
    encoded = value.encode("ascii") + b"\0"
    if description:
        # ASCII description, then empty Unicode and Macintosh descriptions.
        return b"desc" + b"\0" * 4 + struct.pack(">I", len(encoded)) + encoded + b"\0" * 78
    return b"text" + b"\0" * 4 + encoded
