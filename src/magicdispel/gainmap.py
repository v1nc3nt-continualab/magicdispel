"""ISO 21496-1 gain-map metadata: the numbers that render a photo's HDR version.

JPEG files carry it in an APP2 segment after the ISO namespace, where a
primary image's segment may hold only the version fields. HEIF and AVIF files
carry it in a tmap item, after a version byte. `size` gives the length of the
fields the standard defines; decoders read nothing after them, so JPEG drops
what follows and HEIF, which cannot shorten an item in place, refuses it.
"""
import struct

VERSIONS = 4  # minimum_version and writer_version, 16 bits each
# Flags: three channels rather than one, and the base image's color space,
# which leaves the layout alone. The six other bits are reserved and ignored,
# as libavif and libultrahdr ignore them; libultrahdr still reads files from
# drafts of the standard that set bit 3 for one denominator shared by every
# fraction.
MULTICHANNEL, COMMON_DENOMINATOR = 0x80, 0x08
HEADROOMS = 2  # base and alternate HDR headroom
FRACTIONS = 5  # per channel: gain map min, max and gamma, base and alternate offset


class GainMapError(ValueError):
    """Metadata in a version or layout this module does not know."""


def size(data, full=False):
    """The length of the metadata block at the start of `data`: the version
    fields alone (not when `full` is set), or the version fields, the flags,
    and every fraction the flags call for. Decoders refuse a zero
    denominator, and so does this."""
    if len(data) < VERSIONS:
        raise GainMapError("truncated")
    minimum = struct.unpack_from(">H", data)[0]
    if minimum != 0:  # a decoder of version 0 may not read it
        raise GainMapError("minimum version %d" % minimum)
    if len(data) == VERSIONS:
        if full:
            raise GainMapError("no metadata")
        return VERSIONS
    flags, start = data[VERSIONS], VERSIONS + 1
    fractions = HEADROOMS + FRACTIONS * (3 if flags & MULTICHANNEL else 1)
    if flags & COMMON_DENOMINATOR:  # the denominator, then the numerators
        end = start + 4 * (1 + fractions)
        denominators = [data[start:start + 4]]
    else:  # a numerator and a denominator each
        end = start + 8 * fractions
        denominators = [data[offset:offset + 4] for offset in range(start + 4, end, 8)]
    if len(data) < end:
        raise GainMapError("%d bytes where the layout has %d" % (len(data), end))
    if bytes(4) in denominators:
        raise GainMapError("a zero denominator")
    return end
