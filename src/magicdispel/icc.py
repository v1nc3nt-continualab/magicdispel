"""ICC color profiles: keep the color conversion data, drop where the profile came from.

`sanitize` rebuilds a profile at the same byte length with only its color
tags, in one order, a neutral description and copyright, a fixed date and no
creator, maker, model, CMM, platform or profile ID. Of the header it keeps
the bits the standard defines, and zeroes its reserved and vendor bits. It
checks that the color data is unchanged, and refuses tags it does not know
rather than copying them. A kept tag is copied as it is, so every byte of it
must be part of its type's layout (ICC.1 section 10), or zero: reserved
fields, padding and unused space; a field the standard gives a list of
values must hold one of them.
"""
import struct


class ProfileError(ValueError):
    """A profile that cannot be sanitized safely."""


MAX_SIZE = 64 * 1024 * 1024
# Header fields (ICC.1 section 7.2) kept in a sanitized profile: the version,
# the classes and spaces, the bits the standard defines of the flags, device
# attributes and rendering intent, and the D50 illuminant. The version's two
# reserved bytes, the other bits of those fields, reserved or for vendors, and
# everything else in the header - CMM, platform, maker, model, creator,
# profile ID - are zero.
CLASS_AND_SPACES = slice(12, 24)   # device class, color space, connection space
DATE = slice(24, 36)
SIGNATURE = slice(36, 40)          # "acsp"
FLAGS = slice(44, 48)
ATTRIBUTES = slice(56, 64)
INTENT = slice(64, 68)
ILLUMINANT = slice(68, 80)
FLAG_BITS = 0x3                    # embedded, not to be used apart from its image (7.2.11)
ATTRIBUTE_BITS = 0xF               # transparency, matte, negative, black and white (7.2.14)
INTENT_BITS = 0xFFFF               # the upper half is reserved (7.2.15)
INTENTS = range(4)                 # perceptual, relative colorimetric, saturation, absolute
D50 = (0.9642, 1.0, 0.8249)        # the connection space's illuminant, to four decimals (7.2.16)
D50_ILLUMINANT = struct.pack(">3i", 0xF6D6, 0x10000, 0xD32D)
CLASSES = {b"scnr", b"mntr", b"prtr", b"link", b"spac", b"abst", b"nmcl"}
SPACES = {b"XYZ ", b"Lab ", b"Luv ", b"YCbr", b"Yxy ", b"RGB ", b"GRAY", b"HSV ", b"HLS ", b"CMYK",
          b"CMY "} | {b"%cCLR" % digit for digit in b"23456789ABCDEF"}
CONNECTION_SPACES = {b"XYZ ", b"Lab "}  # a device link names its output space there instead
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
    b"hdgm": {b"gmap"}, b"HAGC": {b"hagc"},
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
# The signatures the standard lists for tags of signatureType: the technology,
# the image state of the colorimetric intents, the perceptual and saturation
# intents' reference medium gamut.
SIGNATURES = {
    b"tech": {b"fscn", b"dcam", b"rscn", b"ijet", b"twax", b"epho", b"esta", b"dsub", b"rpho", b"fprn", b"vidm",
              b"vidc", b"pjtv", b"CRT ", b"PMD ", b"AMD ", b"KPCD", b"imgs", b"grav", b"offs", b"silk", b"flex",
              b"mpfs", b"mpfr", b"dmpc", b"dcpj"},
    b"ciis": {b"scoe", b"sape", b"fpce", b"rhoc", b"rpoc"},
    b"rig0": {b"prmg"}, b"rig2": {b"prmg"},
}
OBSERVERS = GEOMETRIES = 3  # unknown, then two of each (measurementType)
ILLUMINANTS = 9             # unknown, D50, D65, D93, F2, D55, A, E, F8
FULL_FLARE = 0x10000        # 100%, as a u16Fixed16Number
COLORANTS = 5               # unknown, BT.709, SMPTE RP 145, EBU Tech 3213, P22 (chromaticityType)
# parametricCurveType: number of parameters for each function type.
PARAMETRIC_FUNCTIONS = {0: 1, 1: 3, 2: 4, 3: 5, 4: 7}
# Types of a fixed size: the type signature, 4 reserved bytes, then data up to here.
FIXED_SIZES = {b"XYZ ": 20, b"sf32": 44, b"cicp": 12, b"sig ": 12, b"view": 36, b"meas": 36}
MATRIX = 48  # the 3x3 matrix and offsets of a lutAToB or lutBToA transform
# Apple's legacy HDR "gmap" curve: after the type and size, the offsets of its
# fields, the first of them a header. Then the identifier of the image it came
# from (cleared), seven numbers, and the curve itself. The fields start at 98
# in the tags written up to iOS 26, one zero byte earlier from iOS 27; only
# those two verified layouts are accepted. The header's fourth byte seems to
# name the primaries as H.273 does: 12, Display P3, in iPhone photos, and 9,
# BT.2020, in Apple's own profiles.
GMAP_FIELDS = (98, 97)
GMAP_HEADERS = {b"\x01\x00\x08" + bytes([primaries]) + bytes(4) for primaries in (12, 9)}
IDENTIFIER = 8     # bytes from the fields to the identifier,
CURVE = 52         # and to the curve
# Apple's Headroom Adaptive Gain Curve (tag HAGC, type hagc; ICC White Paper
# 62): the type, 4 reserved bytes, a byte count, then from GAIN_CURVE_DATA
# SMPTE ST 2094-50 tone-mapping metadata in its binary form (Annex C): flags,
# headrooms and the points of up to four gain curves, nothing else.
GAIN_CURVE_DATA = 12
ALTERNATE_IMAGES = 4


def sanitize(profile):
    """The profile rebuilt at the same length; dropped text and dead space are zero."""
    head, color = color_signature(profile)
    version = profile[8]
    tags = {b"desc": text_tag("Clean", version, description=True), b"cprt": text_tag("", version)}
    tags.update(sorted(color.items()))  # in one order: the original's could carry data
    clean = bytearray(len(profile))
    clean[:TAG_COUNT] = head
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
    """Everything that determines color conversion: the header and color tags."""
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
        elif tag == b"HAGC":
            check_gain_curve(value)
            kept[tag] = value
        else:
            check_layout(value)
            check_values(tag, value)
            kept[tag] = value
    return header(profile), kept


def header(profile):
    """The sanitized profile's header, but for its size: the version, classes
    and spaces, and the defined bits of the flags, device attributes and
    rendering intent, with a fixed date and the D50 illuminant as the standard
    encodes it. A field the standard gives a list of values must hold one of
    them, and the illuminant must be D50 to four decimals."""
    device, space, connection = profile[12:16], profile[16:20], profile[20:24]
    digits = divmod(profile[9], 16)  # the minor and bug-fix version, a decimal digit each
    intent = int.from_bytes(profile[INTENT], "big") & INTENT_BITS
    if (max(digits) > 9 or device not in CLASSES or space not in SPACES or intent not in INTENTS
            or connection not in (SPACES if device == b"link" else CONNECTION_SPACES)):
        raise ProfileError("ICC profile header holds a value the standard does not define")
    illuminant = struct.unpack_from(">3i", profile, ILLUMINANT.start)
    if tuple(round(value / 65536, 4) for value in illuminant) != D50:
        raise ProfileError("ICC profile connection space is not D50")
    clean = bytearray(TAG_COUNT)  # the header ends where the tag count begins
    clean[8:10] = profile[8:10]  # the major version, then the minor and bug-fix one
    clean[CLASS_AND_SPACES] = profile[CLASS_AND_SPACES]
    clean[DATE], clean[SIGNATURE] = PLACEHOLDER_DATE, b"acsp"
    for field, bits in ((FLAGS, FLAG_BITS), (ATTRIBUTES, ATTRIBUTE_BITS)):
        clean[field] = (int.from_bytes(profile[field], "big") & bits).to_bytes(field.stop - field.start, "big")
    clean[INTENT] = struct.pack(">I", intent)
    clean[ILLUMINANT] = D50_ILLUMINANT
    return bytes(clean)


def check_values(tag, value):
    """Refuse a field of a color tag whose values the standard lists that
    holds another: a signature, a measurement's observer, geometry, flare and
    illuminant, the viewing conditions' illuminant, the chromaticities'
    colorant type, cicp's full range flag."""
    kind = value[:4]
    if kind == b"sig ":
        valid = value[8:12] in SIGNATURES[tag]
    elif kind == b"meas":  # the backing's XYZ comes after the observer
        observer, geometry, flare, illuminant = struct.unpack_from(">I12xIII", value, 8)
        valid = observer < OBSERVERS and geometry < GEOMETRIES and flare <= FULL_FLARE and illuminant < ILLUMINANTS
    elif kind == b"view":  # after the illuminant's and the surround's XYZ
        valid = struct.unpack_from(">I", value, 32)[0] < ILLUMINANTS
    elif kind == b"chrm":  # after the channel count
        valid = struct.unpack_from(">H", value, 10)[0] < COLORANTS
    elif kind == b"cicp":
        valid = value[11] < 2
    else:
        return
    if not valid:
        raise ProfileError("ICC %s tag holds a value the standard does not define" % tag.decode("latin-1"))


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
    curve. Only the layouts this was verified on are accepted; no offsets are guessed."""
    for start in GMAP_FIELDS:
        identifier = start + IDENTIFIER
        if (len(value) >= start + CURVE + 8 and value[:12] == b"gmap" + bytes(8)
                and struct.unpack_from(">I", value, 12)[0] == len(value)
                and struct.unpack_from(">5I", value, 16) == (start, identifier, identifier, identifier, 0)
                and value[60:64] == b"A2B0" and not any(value[64:start])
                and value[start:identifier] in GMAP_HEADERS):
            break
    else:
        raise ProfileError("Unsupported HDR adaptive curve metadata layout")
    # The channels' curves, usually one shared by all three: each a zero
    # field, a point count and 12 bytes per point, together filling the rest.
    position = start + CURVE
    for start, length in sorted({struct.unpack_from(">II", value, offset) for offset in (36, 44, 52)}):
        if (start != position or start + 8 > len(value) or any(value[start:start + 4])
                or length != 8 + 12 * struct.unpack_from(">I", value, start + 4)[0]):
            raise ProfileError("Invalid HDR adaptive curve data range")
        position = start + length
    if position != len(value):
        raise ProfileError("Invalid HDR adaptive curve data range")
    return value[:identifier] + bytes(16) + value[identifier + 16:]


def check_gain_curve(value):
    """Refuse an HAGC tag holding anything but one version 0 ST 2094-50 record:
    every reserved bit zero, every number within its range, at most four
    alternate images, and only zero padding after it. Its byte count reaches
    at least to the record's last byte but one, as iOS 27 writes it, and at
    most to the end of the tag, padding included."""
    if len(value) <= GAIN_CURVE_DATA or value[:8] != b"hagc" + bytes(4):
        raise ProfileError("Unsupported HDR gain curve metadata layout")
    try:
        end = gain_curve_end(value)
    except IndexError:
        raise ProfileError("Truncated HDR gain curve metadata")
    count, = struct.unpack_from(">I", value, 8)
    if any(value[end:]) or not end - 1 <= GAIN_CURVE_DATA + count <= len(value):
        raise ProfileError("HDR gain curve metadata holds data outside its layout")


class Bits:
    """The big-endian bit fields of ST 2094-50 metadata, read in order."""

    def __init__(self, data, start):
        self.data, self.position = data, 8 * start

    def read(self, bits):
        value = 0
        for _ in range(bits):
            value = value << 1 | self.data[self.position >> 3] >> (7 - self.position % 8) & 1
            self.position += 1
        return value

    def reserved(self, bits):
        if self.read(bits):
            raise ProfileError("HDR gain curve metadata sets a reserved bit")

    def numbers(self, count, low, high):
        """`count` 16-bit numbers, each from low to high."""
        values = [self.read(16) for _ in range(count)]
        if not all(low <= number <= high for number in values):
            raise ProfileError("HDR gain curve metadata holds a number out of range")
        return values


def gain_curve_end(value):
    """Where the ST 2094-50 record of an HAGC tag ends, following Tables C.1 to
    C.5 of its Annex C, with the ranges of clause C.3. Every group of flags
    fills a byte, so the record ends on one."""
    bits = Bits(value, GAIN_CURVE_DATA)
    if bits.read(3) or bits.read(3):  # the version and the minimum a reader needs: 0 is the only one
        raise ProfileError("Unsupported HDR gain curve metadata version")
    bits.reserved(2)
    custom_white, tone_map = bits.read(1), bits.read(1)
    bits.reserved(6)
    bits.numbers(custom_white, 1, 50000)                     # HDR reference white
    if not tone_map:
        return bits.position // 8
    bits.numbers(1, 0, 60000)                                # baseline HDR headroom
    if bits.read(1):                                         # reference white tone mapping: no curves
        bits.reserved(7)
        return bits.position // 8
    images, primaries, common_mix, common_curve = bits.read(3), bits.read(2), bits.read(1), bits.read(1)
    if images > ALTERNATE_IMAGES:
        raise ProfileError("HDR gain curve metadata has more than four alternate images")
    bits.numbers(8 * (primaries == 3), 0, 50000)             # the gain application space's chromaticities
    for index in range(images):
        bits.numbers(1, 0, 60000)                            # the alternate image's headroom
        if index == 0 or not common_mix:
            if bits.read(2) == 3:                            # a mix of six optional coefficients
                bits.numbers(sum(bits.read(1) for _ in range(6)), 0, 50000)
            else:
                bits.reserved(6)
        if index == 0 or not common_curve:
            points, derived_slopes = bits.read(5) + 1, bits.read(1)
            bits.reserved(2)
            bits.numbers(points, 0, 64000)                   # each control point's x
        bits.numbers(points, 0, 60000)                       # its y
        if not derived_slopes:
            bits.numbers(points, 1, 35999)                   # and its slope angle
    return bits.position // 8


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
