"""ISO 21496-1 gain-map metadata: the numbers that render a photo's HDR version.

JPEG files carry it in an APP2 segment after the ISO namespace, where a
primary image's segment may hold only the version fields. HEIF and AVIF files
carry it in a tmap item, after a version byte. `check` accepts exactly the
layout the standard defines, so the metadata cannot carry anything else along.
"""
import struct

VERSIONS = 4  # minimum_version and writer_version, 16 bits each
# Flags: three channels rather than one, the base image's color space, one
# denominator for every fraction, and the direction the gain map maps in.
MULTICHANNEL, BASE_COLOR_SPACE, COMMON_DENOMINATOR, BACKWARD = 0x80, 0x40, 0x08, 0x04
KNOWN_FLAGS = MULTICHANNEL | BASE_COLOR_SPACE | COMMON_DENOMINATOR | BACKWARD
HEADROOMS = 2  # base and alternate HDR headroom
FRACTIONS = 5  # per channel: gain map min, max and gamma, base and alternate offset


class GainMapError(ValueError):
    """Metadata in a version or layout this module does not know."""


def check(data, full=False):
    """Raise GainMapError unless `data` is exactly one metadata block: the
    version fields alone (not when `full` is set), or the version fields, the
    flags, and every fraction the flags call for."""
    if len(data) < VERSIONS:
        raise GainMapError("truncated")
    versions = struct.unpack_from(">HH", data)
    if versions != (0, 0):
        raise GainMapError("version %d.%d" % versions)
    if len(data) == VERSIONS and not full:
        return
    if len(data) == VERSIONS or data[VERSIONS] & ~KNOWN_FLAGS & 0xFF:
        raise GainMapError("unknown flags")
    fractions = HEADROOMS + FRACTIONS * (3 if data[VERSIONS] & MULTICHANNEL else 1)
    if data[VERSIONS] & COMMON_DENOMINATOR:
        size = VERSIONS + 1 + 4 + 4 * fractions  # the denominator, then the numerators
    else:
        size = VERSIONS + 1 + 8 * fractions  # a numerator and a denominator each
    if len(data) != size:
        raise GainMapError("%d bytes where the layout has %d" % (len(data), size))
