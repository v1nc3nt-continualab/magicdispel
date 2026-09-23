"""The few EXIF fields that change how an image is displayed.

`display_fields` reads only those fields from an EXIF (TIFF-structured) block.
`build` writes a fresh, minimal block from them, so nothing else in the
original EXIF can carry over. Reading is lenient: a field that cannot be read
is treated as absent, as a viewer would.
"""
import struct
from dataclasses import dataclass

BYTE, SHORT, LONG, RATIONAL, UNDEFINED, SRATIONAL, IFD = 1, 3, 4, 5, 7, 10, 13
TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4}

ORIENTATION, X_RESOLUTION, Y_RESOLUTION, RESOLUTION_UNIT = 0x0112, 0x011A, 0x011B, 0x0128
EXIF_POINTER, INTEROP_POINTER, COLOR_SPACE, INTEROP_INDEX = 0x8769, 0xA005, 0xA001, 0x0001
MAKER_NOTE = 0x927C
# Apple's maker note: a header, then an IFD whose offsets count from the note's start.
APPLE_NOTE = b"Apple iOS\0\0\1"
APPLE_HDR_TAGS = (0x21, 0x30)  # HDR headroom and HDR gain, used to render HDR photos


@dataclass(frozen=True)
class DisplayFields:
    orientation: int | None = None      # 1-8; how the stored pixels are turned for display
    resolution: tuple | None = None     # ((x_num, x_den), (y_num, y_den), unit) - print/display size
    color_space: int | None = None      # 1 = sRGB, 0xFFFF = uncalibrated (see interop_index)
    interop_index: bytes | None = None  # b"R98" (sRGB) or b"R03" (Adobe RGB) for DCF files
    apple_hdr: bytes | None = None      # a fresh Apple maker note holding only the HDR values


def display_fields(data):
    """Read the display fields from EXIF data, with or without an Exif\\0\\0 prefix."""
    if data.startswith(b"Exif\0\0"):
        data = data[6:]
    reader = _Reader(data)
    ifd0 = reader.directory(reader.first_directory())
    exif_ifd = reader.directory(reader.pointer(ifd0.get(EXIF_POINTER)))
    interop = reader.directory(reader.pointer(exif_ifd.get(INTEROP_POINTER)))
    orientation = reader.number(ifd0.get(ORIENTATION), SHORT)
    x, y = reader.rational(ifd0.get(X_RESOLUTION)), reader.rational(ifd0.get(Y_RESOLUTION))
    unit = reader.number(ifd0.get(RESOLUTION_UNIT), SHORT) or 2
    index = reader.text(interop.get(INTEROP_INDEX))
    note = exif_ifd.get(MAKER_NOTE)
    return DisplayFields(
        orientation=orientation if orientation in range(1, 9) else None,
        resolution=(x, y, unit) if x and y and unit in (1, 2, 3) else None,
        color_space=reader.number(exif_ifd.get(COLOR_SPACE), SHORT),
        interop_index=index if index in (b"R98", b"R03") else None,
        # The standard type is UNDEFINED; some writers (Pillow among them) use BYTE.
        apple_hdr=apple_hdr_note(note[2]) if note and note[0] in (BYTE, UNDEFINED) else None,
    )


def apple_hdr_note(note):
    """A new Apple maker note with only the HDR headroom and gain, or None."""
    if not note.startswith(APPLE_NOTE):
        return None
    reader = _Reader(note, byte_order=note[12:14])
    count = reader.unpack("H", 14)
    values = []
    for index in range(count[0] if count else 0):
        entry = reader.unpack("HHII", 16 + 12 * index)
        if entry is None:
            return None
        tag, kind, number, offset = entry
        if tag in APPLE_HDR_TAGS and kind == SRATIONAL and number == 1:
            value = reader.unpack("ii", offset)
            if value is None or value[1] == 0:
                return None
            values.append((tag, value))
    if not values:
        return None
    # Big-endian, entries in tag order, values right after the empty next-IFD link.
    header = APPLE_NOTE + b"MM" + struct.pack(">H", len(values))
    table = b"".join(struct.pack(">HHII", tag, SRATIONAL, 1, 16 + 12 * len(values) + 4 + 8 * n)
                     for n, (tag, _) in enumerate(values))
    return header + table + b"\0" * 4 + b"".join(struct.pack(">ii", *value) for _, value in values)


def build(fields):
    """A minimal big-endian EXIF block holding only `fields`, or b"" if empty."""
    ifd0, exif_ifd, interop = [], [], []
    if fields.orientation:
        ifd0.append((ORIENTATION, SHORT, struct.pack(">H", fields.orientation)))
    if fields.resolution:
        (x, y, unit) = fields.resolution
        ifd0 += [(X_RESOLUTION, RATIONAL, struct.pack(">II", *x)),
                 (Y_RESOLUTION, RATIONAL, struct.pack(">II", *y)),
                 (RESOLUTION_UNIT, SHORT, struct.pack(">H", unit))]
    if fields.color_space is not None:
        exif_ifd.append((COLOR_SPACE, SHORT, struct.pack(">H", fields.color_space)))
    if fields.apple_hdr:
        exif_ifd.append((MAKER_NOTE, UNDEFINED, fields.apple_hdr))
    if fields.interop_index:
        interop.append((INTEROP_INDEX, 2, fields.interop_index + b"\0"))
    if not (ifd0 or exif_ifd or interop):
        return b""
    # Directories are laid out in order: IFD0, Exif IFD, Interop IFD. Pointer
    # values do not change a directory's size, so sizes are known up front.
    layout = [ifd0 + ([(EXIF_POINTER, LONG, None)] if exif_ifd or interop else []),
              exif_ifd + ([(INTEROP_POINTER, LONG, None)] if interop else []),
              interop]
    layout = [entries for entries in layout if entries]
    offsets = [8]
    for entries in layout[:-1]:
        offsets.append(offsets[-1] + _directory_size(entries))
    blocks = []
    for index, entries in enumerate(layout):
        pointer = struct.pack(">I", offsets[index + 1]) if index + 1 < len(layout) else None
        resolved = [(tag, kind, pointer if value is None else value) for tag, kind, value in entries]
        blocks.append(_directory(resolved, offsets[index]))
    return b"MM\0*" + struct.pack(">I", 8) + b"".join(blocks)


def _directory_size(entries):
    extra = sum(len(value) + len(value) % 2 for _, _, value in entries
                if value is not None and len(value) > 4)
    return 2 + 12 * len(entries) + 4 + extra


def _directory(entries, start):
    entries = sorted(entries)
    table, extra = struct.pack(">H", len(entries)), b""
    data_start = start + 2 + 12 * len(entries) + 4
    for tag, kind, value in entries:
        count = len(value) // TYPE_SIZES[kind]
        if len(value) <= 4:
            table += struct.pack(">HHI", tag, kind, count) + value.ljust(4, b"\0")
        else:
            table += struct.pack(">HHII", tag, kind, count, data_start + len(extra))
            extra += value + b"\0" * (len(value) % 2)
    return table + struct.pack(">I", 0) + extra


class _Reader:
    """Bounds-checked access to one TIFF-structured block; problems read as absent."""

    def __init__(self, data, byte_order=None):
        self.data = data
        self.order = {b"II": "<", b"MM": ">"}.get(data[:2] if byte_order is None else byte_order)

    def unpack(self, fmt, offset):
        if self.order is None or offset < 0 or offset + struct.calcsize(self.order + fmt) > len(self.data):
            return None
        return struct.unpack_from(self.order + fmt, self.data, offset)

    def first_directory(self):
        header = self.unpack("HI", 2)
        return header[1] if header and header[0] == 42 else None

    def directory(self, offset):
        """{tag: (type, count, value bytes)} for one IFD; {} if unreadable."""
        count = self.unpack("H", offset) if offset else None
        if not count:
            return {}
        entries = {}
        for index in range(count[0]):
            entry = self.unpack("HHII", offset + 2 + 12 * index)
            if entry is None:
                return {}
            tag, kind, number, value = entry
            size = TYPE_SIZES.get(kind, 0) * number
            start = offset + 2 + 12 * index + 8 if size <= 4 else value
            if not size or start + size > len(self.data):
                continue
            entries[tag] = (kind, number, self.data[start:start + size])
        return entries

    def number(self, entry, kind):
        if entry is None or entry[0] != kind or entry[1] != 1:
            return None
        return struct.unpack(self.order + {SHORT: "H", LONG: "I"}[kind], entry[2])[0]

    def pointer(self, entry):
        if entry is not None and entry[0] == IFD:
            entry = (LONG,) + entry[1:]
        return self.number(entry, LONG)

    def rational(self, entry):
        if entry is None or entry[0] != RATIONAL or entry[1] != 1:
            return None
        numerator, denominator = struct.unpack(self.order + "II", entry[2])
        return (numerator, denominator) if numerator and denominator else None

    def text(self, entry):
        return entry[2].rstrip(b"\0") if entry and entry[0] == 2 else None
