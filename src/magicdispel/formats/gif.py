"""GIF: rebuild the file from the blocks needed to show the image.

Copied unchanged: the header, screen descriptor and global color table, and
every image with its local color table and compressed data, which is put in
sub-blocks of 255 bytes again, and whose descriptor loses its reserved bits.
Graphic control extensions (frame timing and transparency) are kept, the last
before an image, with reserved bits cleared, the animation loop count is
rewritten as a bare NETSCAPE2.0 (or ANIMEXTS1.0) extension, once, and the ICC
profile is sanitized, once. Comments, plain-text overlays, XMP and other
application extensions, and anything after the trailer are dropped.
"""
from .. import icc
from ..errors import FormatError, VerificationError

IMAGE, EXTENSION, TRAILER = 0x2C, 0x21, b"\x3b"          # block introducers
GRAPHIC_CONTROL, APPLICATION = 0xF9, 0xFF                  # extension labels
SCREEN = 13            # signature, version and logical screen descriptor
IMAGE_DESCRIPTOR = 10  # its last byte holds the local color table flags
LOOP_APPLICATIONS = {b"NETSCAPE2.0", b"ANIMEXTS1.0"}
ICC_APPLICATION = b"ICCRGBG1012"
IMAGE_RESERVED = 0x18  # bits of an image descriptor's flags that mean nothing
MAX_DROPPED = 1024     # blocks that go: real files hold a few, and Pillow's time grows with their square


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
    result, control, seen, dropped = [], None, set(), 0
    for kind, block in blocks(data):
        if kind in ("header", "trailer"):
            result.append(block)
        elif kind == "image":
            result.extend([control] if control else [])
            result.append(normalized(block))
            control = None
        elif kind == GRAPHIC_CONTROL:  # size 4, flags, delay, transparent index
            if len(block) != 8 or block[2] != 4:
                raise damaged()
            control = block[:3] + bytes([block[3] & 0x1F]) + block[4:]  # of two in a row, the last counts
        else:
            kept = application_extension(block) if kind == APPLICATION else []
            if kept and kept[0][3:14] not in seen:  # once each: the loop count, the profile
                seen.add(kept[0][3:14])
                result.extend(kept)
            else:
                dropped += 1
                if dropped > MAX_DROPPED:
                    raise FormatError("unsupported_part", format="GIF", part="over a thousand extension blocks")
    return result


def normalized(block):
    """An image block with the reserved bits of its descriptor cleared and its
    data in sub-blocks of 255 bytes: how it is cut says nothing about the image."""
    flags = block[IMAGE_DESCRIPTOR - 1]
    start = IMAGE_DESCRIPTOR + color_table_size(flags) + 1  # after the LZW code size
    head = block[:IMAGE_DESCRIPTOR - 1] + bytes([flags & ~IMAGE_RESERVED]) + block[IMAGE_DESCRIPTOR:start]
    data = b"".join(sub_blocks(block, start))
    pieces = [data[n:n + 255] for n in range(0, len(data), 255)]
    return head + b"".join(bytes([len(piece)]) + piece for piece in pieces) + b"\0"


def application_extension(block):
    """A kept application extension, rebuilt: [] when it is dropped."""
    pieces = sub_blocks(block, 2)
    if not pieces or len(pieces[0]) != 11:
        raise damaged()
    application, data = pieces[0], pieces[1:]
    if application in LOOP_APPLICATIONS:
        loops = [piece for piece in data if len(piece) == 3 and piece[0] == 1]
        return [extension(APPLICATION, [application, loops[0]])] if loops else []
    if application == ICC_APPLICATION:
        try:
            profile = icc.sanitize(b"".join(data))
        except icc.ProfileError as error:
            raise FormatError("unsupported_profile", format="GIF", detail=str(error))
        return [extension(APPLICATION, [application] + [profile[n:n + 255] for n in range(0, len(profile), 255)])]
    return []


def blocks(data):
    """("header" | "image" | extension label | "trailer", bytes) up to the
    trailer. A file that ends after a complete block gets a trailer."""
    if data[:6] not in (b"GIF87a", b"GIF89a") or len(data) < SCREEN:
        raise damaged()
    position = SCREEN + color_table_size(data[SCREEN - 3])  # the screen's color table flags
    if position > len(data):
        raise damaged()
    yield "header", data[:position]
    while position < len(data):
        start, introducer = position, data[position]
        if introducer == TRAILER[0]:
            yield "trailer", TRAILER
            return
        if introducer == IMAGE:
            if position + IMAGE_DESCRIPTOR > len(data):
                raise damaged()
            # Descriptor, local color table, then the LZW code size and data.
            tables = color_table_size(data[position + IMAGE_DESCRIPTOR - 1])
            position = skip_sub_blocks(data, position + IMAGE_DESCRIPTOR + tables + 1)
            yield "image", data[start:position]
        elif introducer == EXTENSION and position + 2 <= len(data):
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
    return bytes([EXTENSION, label]) + b"".join(bytes([len(piece)]) + piece for piece in pieces) + b"\0"


def damaged():
    return FormatError("damaged", format="GIF")
