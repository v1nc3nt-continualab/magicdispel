"""ICC color profiles: keep the color conversion data, drop where the profile came from.

`sanitize` rebuilds a profile at the same byte length with only its color
tags, a neutral description and copyright, a fixed date and no creator,
maker, model, CMM, platform or profile ID. It checks that the color data is
unchanged, and refuses tags it does not know rather than copying them.
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
for _n in range(4):
    COLOR_TYPES[b"D2B%d" % _n] = {b"mpet"}
    COLOR_TYPES[b"B2D%d" % _n] = {b"mpet"}
APPLE_CURVES = {b"aarg", b"aagg", b"aabg"}
# parametricCurveType: number of parameters for each function type.
PARAMETRIC_FUNCTIONS = {0: 1, 1: 3, 2: 4, 3: 5, 4: 7}
# Apple's legacy HDR "gmap" curve stores the identifier of the image it came
# from here; the rest of the tag is the numeric curve.
GMAP_IDENTIFIER = slice(106, 122)


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
        kept[tag] = sanitize_adaptive_curve(value) if tag == b"hdgm" else value
    return profile[CLASS_AND_SPACES], profile[FLAGS], profile[RENDERING], kept


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
    if (len(value) < 158 or value[:12] != b"gmap" + b"\0" * 8
            or struct.unpack_from(">I", value, 12)[0] != len(value)
            or struct.unpack_from(">5I", value, 16) != (98, 106, 106, 106, 0)
            or value[60:64] != b"A2B0" or any(value[64:96])
            or value[98:106] != b"\x01\x00\x08\x0c\0\0\0\0"):
        raise ProfileError("Unsupported HDR adaptive curve metadata layout")
    for offset in (36, 44, 52):
        start, length = struct.unpack_from(">II", value, offset)
        if start < 150 or length < 8 or start + length > len(value):
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
