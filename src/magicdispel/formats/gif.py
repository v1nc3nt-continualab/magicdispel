"""GIF: rebuild the file from the blocks needed to show the image.

Copied unchanged: the header, screen descriptor and global color table, and
every image with its local color table and compressed data. Graphic control
extensions (frame timing and transparency) are kept with reserved bits
cleared, the animation loop count is rewritten as a bare NETSCAPE2.0 (or
ANIMEXTS1.0) extension, and the ICC profile is sanitized. Comments, plain-text
overlays, XMP and other application extensions, and anything after the
trailer are dropped.
"""
from ..errors import FormatError, VerificationError
from ..privacy import PrivacyError, sanitize_icc

TRAILER = b"\x3b"
LOOP_APPLICATIONS = {b"NETSCAPE2.0", b"ANIMEXTS1.0"}
ICC_APPLICATION = b"ICCRGBG1012"


def rebuild(data):
    return b"".join(selected_blocks(data))


def verify(original, rebuilt):
    """Parse the result on its own: the file ends at its trailer and holds
    exactly the blocks the original should yield."""
    parsed = [block for _, block in blocks(rebuilt)]
    if b"".join(parsed) != rebuilt or parsed[-1] != TRAILER:
        raise VerificationError("verification_failed", detail="unexpected GIF structure")
    if parsed != selected_blocks(original):
        raise VerificationError("verification_failed", detail="GIF blocks differ from the original")


def selected_blocks(data):
    """The blocks, as bytes, that a clean copy holds."""
    result = []
    for kind, block in blocks(data):
        if kind in ("header", "image", "trailer"):
            result.append(block)
        elif kind == 0xF9:  # graphic control: size 4, flags, delay, transparent index
            if len(block) != 8 or block[2] != 4:
                raise damaged()
            result.append(block[:3] + bytes([block[3] & 0x1F]) + block[4:])
        elif kind == 0xFF:
            result.extend(application_extension(block))
    return result


def application_extension(block):
    """A kept application extension, rebuilt: [] when it is dropped."""
    pieces = sub_blocks(block, 2)
    if not pieces or len(pieces[0]) != 11:
        raise damaged()
    application, data = pieces[0], pieces[1:]
    if application in LOOP_APPLICATIONS:
        loops = [piece for piece in data if len(piece) == 3 and piece[0] == 1]
        return [extension(0xFF, [application, loops[0]])] if loops else []
    if application == ICC_APPLICATION:
        try:
            profile = sanitize_icc(b"".join(data))
        except PrivacyError as error:
            raise FormatError("unsupported_profile", format="GIF", detail=str(error))
        return [extension(0xFF, [application] + [profile[n:n + 255] for n in range(0, len(profile), 255)])]
    return []


def blocks(data):
    """("header" | "image" | extension label | "trailer", bytes) up to the
    trailer. A file that ends after a complete block gets a trailer."""
    if data[:6] not in (b"GIF87a", b"GIF89a") or len(data) < 13:
        raise damaged()
    position = 13 + color_table_size(data[10])
    if position > len(data):
        raise damaged()
    yield "header", data[:position]
    while position < len(data):
        start, introducer = position, data[position]
        if introducer == 0x3B:
            yield "trailer", TRAILER
            return
        if introducer == 0x2C:
            if position + 10 > len(data):
                raise damaged()
            # Descriptor, local color table, then the LZW code size and data.
            position = skip_sub_blocks(data, position + 10 + color_table_size(data[position + 9]) + 1)
            yield "image", data[start:position]
        elif introducer == 0x21 and position + 2 <= len(data):
            position = skip_sub_blocks(data, position + 2)
            yield data[start + 1], data[start:position]
        else:
            raise damaged()
    yield "trailer", TRAILER


def color_table_size(flags):
    return 3 << ((flags & 7) + 1) if flags & 0x80 else 0


def skip_sub_blocks(data, position):
    while True:
        if position >= len(data):
            raise damaged()
        size = data[position]
        position += 1 + size
        if not size:
            return position


def sub_blocks(block, start):
    pieces, position = [], start
    while block[position]:
        pieces.append(block[position + 1:position + 1 + block[position]])
        position += 1 + block[position]
    return pieces


def extension(label, pieces):
    return b"\x21" + bytes([label]) + b"".join(bytes([len(piece)]) + piece for piece in pieces) + b"\0"


def damaged():
    return FormatError("damaged", format="GIF")
