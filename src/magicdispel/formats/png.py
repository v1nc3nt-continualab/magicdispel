"""PNG and APNG: rebuild the file from the chunks needed to show the image.

Copied unchanged: the header, palette, transparency, compressed image data,
color description (gAMA, cHRM, sRGB, cICP, sBIT and the HDR chunks mDCV and
cLLI), DPI (pHYs), background color and APNG animation chunks. The ICC profile
is sanitized and EXIF is reduced to the orientation. Everything else is
dropped: text, time stamps, C2PA manifests, Apple's decoding hints (iDOT),
private chunks and anything after IEND. An unknown critical chunk is refused,
because the PNG rules forbid decoders from skipping it.

A chunk that may come once is kept once, the first, and if it comes before the
image data, as decoders read them; the suggested palette of a truecolor image
is dropped. Image data is cut into chunks of one size and APNG sequence
numbers are written afresh, so how a writer framed its data says nothing.
"""
import itertools
import struct
import zlib

from .. import exif, icc, pixels
from ..errors import FormatError, VerificationError

SIGNATURE = b"\x89PNG\r\n\x1a\n"
PROFILE_NAME = b"Clean"
STEP = 1 << 20

# Chunks copied unchanged, with the payload sizes they may have (None: any).
# Fixed sizes leave no room to carry anything besides the specified values.
KEPT = {
    b"IHDR": {13}, b"PLTE": None, b"tRNS": None, b"IDAT": None, b"IEND": {0},
    b"gAMA": {4}, b"cHRM": {32}, b"sRGB": {1}, b"cICP": {4}, b"sBIT": {1, 2, 3, 4},
    b"mDCV": {24}, b"cLLI": {8}, b"pHYs": {9}, b"bKGD": {1, 2, 6},
    b"acTL": {8}, b"fcTL": {26}, b"fdAT": None,
}
REWRITTEN = {b"iCCP", b"eXIf"}
# Chunks that come once, and of those the ones that must come before the image data.
BEFORE_IMAGE = {b"gAMA", b"cHRM", b"sRGB", b"iCCP", b"cICP", b"sBIT", b"mDCV", b"cLLI", b"pHYs", b"bKGD", b"acTL"}
ONCE = BEFORE_IMAGE | {b"eXIf"}
FRAME = 1 << 16  # bytes of image data in each IDAT or fdAT chunk
CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
# Payload sizes of the chunks whose size depends on the color type: bKGD, and sBIT (a byte per channel).
BACKGROUND_SIZES = {0: 2, 2: 6, 3: 1, 4: 2, 6: 6}
BIT_DEPTHS = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}
# Adam7 passes: first column, first row, column step, row step.
ADAM7 = ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2))


def rebuild(data):
    parts = selected_chunks(data)
    check_image_data(parts)
    return SIGNATURE + b"".join(serialize(kind, payload) for kind, payload in parts)


def verify(original, rebuilt):
    """The result must hold exactly the chunks the original should yield."""
    if check_structure(rebuilt) != selected_chunks(original):
        raise VerificationError("verification_failed", detail="PNG chunks differ from the original")


def check_structure(data):
    """Parse a rebuilt file on its own: only allowlisted chunks with valid CRCs,
    and nothing after IEND. Returns its (type, payload) pairs."""
    size, result = len(SIGNATURE), []
    for kind, payload, crc_ok in chunks(data):
        if not crc_ok or (kind not in KEPT and kind not in REWRITTEN):
            raise VerificationError("verification_failed", detail="unexpected PNG chunk " + kind.decode())
        size += 12 + len(payload)
        result.append((kind, payload))
    if size != len(data):
        raise VerificationError("verification_failed", detail="data after the PNG end")
    return result


def selected_chunks(data):
    """The (type, payload) pairs a clean copy holds, in the original's order."""
    parts, seen = [], set()
    for index, (kind, payload, crc_ok) in enumerate(chunks(data)):
        if (index == 0) != (kind == b"IHDR"):
            raise damaged()
        if (kind in KEPT or kind in REWRITTEN) and not crc_ok:
            raise damaged()
        if (kind in ONCE and kind in seen) or (kind in BEFORE_IMAGE and b"IDAT" in seen):
            continue  # a decoder skips a chunk that comes again or too late
        if kind in (b"fcTL", b"fdAT") and b"acTL" not in seen:
            continue  # frames of an animation that is not declared
        if kind == b"PLTE" and parts[0][1][9] in (2, 6):
            continue  # a suggestion, of colors to use on a display of few
        seen.add(kind)
        if kind == b"fdAT" and len(payload) < 4:
            raise damaged()
        if kind in KEPT:
            if KEPT[kind] is not None and len(payload) not in KEPT[kind]:
                raise damaged()
            parts.append((kind, payload))
        elif kind == b"iCCP":
            parts.append((kind, PROFILE_NAME + b"\0\0" + zlib.compress(sanitized_profile(payload))))
        elif kind == b"eXIf":
            orientation = exif.display_fields(payload).orientation
            if orientation not in (None, 1):
                parts.append((kind, exif.build(exif.DisplayFields(orientation=orientation))))
        elif not kind[0] & 0x20:
            raise FormatError("unsupported_part", format="PNG", part=kind.decode())
    return numbered(framed(parts))


def framed(parts):
    """`parts` with each run of IDAT chunks, and of fdAT chunks, cut again into
    chunks of one size."""
    result = []
    for kind, run in itertools.groupby(parts, key=lambda part: part[0]):
        if kind in (b"IDAT", b"fdAT"):
            start = 4 if kind == b"fdAT" else 0  # after a sequence number
            data = b"".join(payload[start:] for _, payload in run)
            result += [(kind, bytes(start) + data[n:n + FRAME]) for n in range(0, len(data), FRAME)]
        else:
            result += run
    return result


def numbered(parts):
    """`parts` with the sequence numbers of fcTL and fdAT chunks counting from 0, as APNG requires."""
    count = itertools.count()
    return [(kind, struct.pack(">I", next(count)) + payload[4:]) if kind in (b"fcTL", b"fdAT") else (kind, payload)
            for kind, payload in parts]


def is_animated(data):
    for kind, _, _ in chunks(data):
        if kind in (b"acTL", b"IDAT"):
            return kind == b"acTL"
    return False


def chunks(data):
    """(type, payload, crc_ok) for each chunk up to and including IEND."""
    if not data.startswith(SIGNATURE):
        raise damaged()
    position = len(SIGNATURE)
    while True:
        if position + 12 > len(data):
            raise damaged()
        length, kind = struct.unpack_from(">I4s", data, position)
        end = position + 12 + length
        if end > len(data) or not kind.isalpha():
            raise damaged()
        payload = data[position + 8:end - 4]
        yield kind, payload, zlib.crc32(kind + payload) == struct.unpack_from(">I", data, end - 4)[0]
        if kind == b"IEND":
            return
        position = end


def serialize(kind, payload):
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))


def sanitized_profile(payload):
    """The ICC profile inside an iCCP chunk (name, method 0, zlib data), sanitized."""
    name_end = payload.find(b"\0")
    if not 1 <= name_end <= 79 or payload[name_end + 1:name_end + 2] != b"\0":
        raise damaged()
    stream = zlib.decompressobj()
    try:
        profile = stream.decompress(payload[name_end + 2:], icc.MAX_SIZE + 1)
    except zlib.error:
        raise damaged()
    if len(profile) > icc.MAX_SIZE or not stream.eof or stream.unused_data or stream.unconsumed_tail:
        raise damaged()
    try:
        return icc.sanitize(profile)
    except icc.ProfileError as error:
        raise FormatError("unsupported_profile", format="PNG", detail=str(error))


def check_image_data(parts):
    """IHDR must be valid, and each image's compressed data must inflate to
    exactly the scanlines it describes, with nothing after the zlib stream."""
    width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", parts[0][1])
    if (not width or not height or color not in CHANNELS or depth not in BIT_DEPTHS[color]
            or compression or filtering or interlace not in (0, 1)):
        raise damaged()
    if width * height > pixels.MEGAPIXELS * 1_000_000:  # before inflating anything
        raise FormatError("too_large", format="PNG", limit=pixels.MEGAPIXELS)
    check_palette(parts, color)
    check_values(parts, color, depth)
    bits = CHANNELS[color] * depth
    check_stream([payload for kind, payload in parts if kind == b"IDAT"],
                 scanline_bytes(width, height, bits, interlace))
    frames = []  # APNG frames stored in fdAT chunks: (width, height, payloads)
    for kind, payload in parts:
        if kind == b"acTL" and struct.unpack(">I", payload[:4])[0] != sum(part[0] == b"fcTL" for part in parts):
            raise damaged()  # a frame count that is not the number of frames: the rest is not shown
        if kind == b"fcTL":
            frame_width, frame_height, x, y = struct.unpack_from(">IIII", payload, 4)
            if not frame_width or not frame_height or x + frame_width > width or y + frame_height > height:
                raise damaged()  # a frame must lie within the image
            frames.append((frame_width, frame_height, []))
        elif kind == b"fdAT":
            if not frames or len(payload) < 4:
                raise damaged()
            frames[-1][2].append(payload[4:])  # after the sequence number
    for frame_width, frame_height, payloads in frames:
        if payloads:
            check_stream(payloads, scanline_bytes(frame_width, frame_height, bits, interlace))


def check_palette(parts, color):
    """PLTE and tRNS hold exactly what the color type allows, nothing more."""
    palettes = [payload for kind, payload in parts if kind == b"PLTE"]
    alphas = [payload for kind, payload in parts if kind == b"tRNS"]
    # A palette image needs PLTE; truecolor ones may suggest one; gray ones may not have one.
    if len(palettes) > 1 or len(alphas) > 1 or (color == 3 and not palettes) or (color in (0, 4) and palettes):
        raise damaged()
    entries = len(palettes[0]) // 3 if palettes else 0
    if palettes and (len(palettes[0]) % 3 or not 1 <= entries <= 256):
        raise damaged()
    # tRNS: a gray level (2 bytes), an RGB color (6), or up to one alpha per palette entry.
    if alphas and len(alphas[0]) not in {0: {2}, 2: {6}, 3: set(range(1, entries + 1))}.get(color, ()):
        raise damaged()


def check_values(parts, color, depth):
    """Chunks hold what their standard defines and no more: sizes that fit the
    color type, and values within range."""
    size = {0: 1, 2: 3, 3: 3, 4: 2, 6: 4}[color]  # of sBIT
    entries = sum(len(payload) // 3 for kind, payload in parts if kind == b"PLTE")
    for kind, payload in parts:
        if kind == b"bKGD":
            samples = [payload[0]] if color == 3 else struct.unpack(">%dH" % (len(payload) // 2), payload)
            valid = len(payload) == BACKGROUND_SIZES[color] and all(
                value < (entries if color == 3 else 1 << depth) for value in samples)
        elif kind == b"sBIT":
            valid = len(payload) == size and all(1 <= value <= (8 if color == 3 else depth) for value in payload)
        elif kind == b"sRGB":
            valid = payload[0] <= 3  # a rendering intent
        elif kind == b"cICP":
            valid = payload[2] == 0 and payload[3] <= 1  # RGB, not YCbCr; a full-range flag
        elif kind == b"pHYs":
            valid = payload[8] <= 1  # meters, or no unit
        else:
            continue
        if not valid:
            raise damaged()


def scanline_bytes(width, height, bits, interlace):
    """Size of one image's filtered scanlines: a filter byte per row plus pixels."""
    total = 0
    for column, row, column_step, row_step in (ADAM7 if interlace else ((0, 0, 1, 1),)):
        columns = (width - column + column_step - 1) // column_step if width > column else 0
        rows = (height - row + row_step - 1) // row_step if height > row else 0
        if columns and rows:
            total += rows * (1 + (columns * bits + 7) // 8)
    return total


def check_stream(payloads, expected):
    """The payloads must hold one complete zlib stream inflating to exactly
    `expected` bytes. Inflation runs in bounded steps, so a hostile stream
    cannot exhaust memory."""
    stream, produced = zlib.decompressobj(), 0
    try:
        for payload in payloads:
            if stream.eof:
                if payload:
                    raise FormatError("extra_image_data", format="PNG")
                continue
            pending = payload
            while True:
                output = stream.decompress(pending, STEP)
                produced += len(output)
                if produced > expected:
                    raise FormatError("extra_image_data", format="PNG")
                pending = stream.unconsumed_tail
                # Stop when the stream ends, or all input is used and nothing is held back.
                if stream.eof or (not pending and len(output) < STEP):
                    break
    except zlib.error:
        raise damaged()
    if not stream.eof or produced != expected:
        raise damaged()
    if stream.unused_data:
        raise FormatError("extra_image_data", format="PNG")


def damaged():
    return FormatError("damaged", format="PNG")
