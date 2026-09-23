"""WebP: rebuild the RIFF container from the chunks needed to show the image.

Copied unchanged: the compressed image data (VP8, VP8L, ALPH) and animation
parameters (ANIM, and each ANMF frame with only its image chunks inside). The
extended header (VP8X) gets flags matching what is kept, with reserved bits
cleared. The ICC profile is sanitized and EXIF is reduced to the orientation.
XMP, unknown chunks and anything after the RIFF end are dropped.
"""
import struct

from .. import exif, icc
from ..errors import FormatError, VerificationError

# VP8X flags. Bits 0, 6 and 7 are reserved and always written as zero.
ICC, ALPHA, EXIF, XMP, ANIMATION = 0x20, 0x10, 0x08, 0x04, 0x02
IMAGE_DATA = {b"VP8 ", b"VP8L", b"ALPH"}
KEPT = IMAGE_DATA | {b"VP8X", b"ICCP", b"ANIM", b"ANMF", b"EXIF"}
RIFF_HEADER = 12  # "RIFF", the size of what follows, "WEBP"
# Payload sizes: VP8X flags and canvas size; ANIM background color and loop
# count; the header of each ANMF frame, before its image chunks.
VP8X_SIZE, ANIM_SIZE, FRAME_HEADER = 10, 6, 16


def rebuild(data):
    return serialize(selected_chunks(data))


def verify(original, rebuilt):
    """Parse the result on its own: a RIFF size matching the file, zero padding,
    only allowlisted chunks, and exactly the chunks the original should yield."""
    parts = chunks(rebuilt)
    if serialize(parts) != rebuilt or any(kind not in KEPT for kind, _ in parts):
        raise VerificationError("verification_failed", detail="unexpected WebP structure")
    if parts != selected_chunks(original):
        raise VerificationError("verification_failed", detail="WebP chunks differ from the original")


def selected_chunks(data):
    """The (fourcc, payload) pairs a clean copy holds."""
    parts = chunks(data)
    if parts[0][0] in (b"VP8 ", b"VP8L"):
        return parts[:1]  # the simple format: one image chunk and nothing else
    if parts[0][0] != b"VP8X" or len(parts[0][1]) != VP8X_SIZE:
        raise damaged()
    kept = []
    for kind, payload in parts[1:]:
        if kind in IMAGE_DATA:
            kept.append((kind, payload))
        elif kind == b"ANIM":
            if len(payload) != ANIM_SIZE:
                raise damaged()
            kept.append((kind, payload))
        elif kind == b"ANMF":
            kept.append((kind, animation_frame(payload)))
        elif kind == b"ICCP":
            try:
                kept.append((kind, icc.sanitize(payload)))
            except icc.ProfileError as error:
                raise FormatError("unsupported_profile", format="WebP", detail=str(error))
    if not any(kind in (b"VP8 ", b"VP8L", b"ANMF") for kind, _ in kept):
        raise damaged()
    exif_payloads = [payload for kind, payload in parts if kind == b"EXIF"]
    orientation = exif.display_fields(exif_payloads[0]).orientation if exif_payloads else None
    if orientation not in (None, 1):
        kept.append((b"EXIF", exif.build(exif.DisplayFields(orientation=orientation))))
    kinds = {kind for kind, _ in kept}
    if not kinds & {b"ICCP", b"ALPH", b"ANIM", b"ANMF", b"EXIF"}:
        # Nothing left needs the extended header: use the simple format, as
        # an encoder would have written it.
        return [part for part in kept if part[0] in (b"VP8 ", b"VP8L")][:1]
    flags = parts[0][1][0] & (ALPHA | ANIMATION)
    if b"ICCP" in kinds:
        flags |= ICC
    if b"EXIF" in kinds:
        flags |= EXIF
    return [(b"VP8X", bytes([flags]) + b"\0\0\0" + parts[0][1][4:])] + kept


def animation_frame(payload):
    """An ANMF chunk: the frame header, then only the frame's image chunks."""
    if len(payload) < FRAME_HEADER:
        raise damaged()
    last = FRAME_HEADER - 1
    header = payload[:last] + bytes([payload[last] & 0x03])  # blending and disposal bits only
    inner = [(kind, body) for kind, body in walk(payload, FRAME_HEADER, len(payload)) if kind in IMAGE_DATA]
    if not any(kind in (b"VP8 ", b"VP8L") for kind, _ in inner):
        raise damaged()
    return header + b"".join(chunk(kind, body) for kind, body in inner)


def chunks(data):
    """Top-level (fourcc, payload) pairs inside the RIFF size."""
    if len(data) < RIFF_HEADER + 8 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise damaged()
    end = 8 + struct.unpack_from("<I", data, 4)[0]  # the size counts from after itself
    if end > len(data):
        raise damaged()
    parts = list(walk(data, RIFF_HEADER, end))
    if not parts:
        raise damaged()
    return parts


def walk(data, start, end):
    position = start
    while position < end:
        if position + 8 > end:
            raise damaged()
        kind, size = struct.unpack_from("<4sI", data, position)
        if position + 8 + size > end:
            raise damaged()
        yield kind, data[position + 8:position + 8 + size]
        position += 8 + size + (size & 1)  # a final pad byte may be missing; tolerated


def chunk(kind, payload):
    return kind + struct.pack("<I", len(payload)) + payload + b"\0" * (len(payload) & 1)


def serialize(parts):
    body = b"WEBP" + b"".join(chunk(kind, payload) for kind, payload in parts)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def damaged():
    return FormatError("damaged", format="WebP")
