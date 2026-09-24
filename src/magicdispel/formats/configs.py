"""Decoder configurations in sample entries, read to where they end.

A decoder configuration is copied whole, as the samples are (see movie.py),
but only up to its declared end: the parameter sets of avcC, hvcC and lhvC,
the descriptors of esds, the configuration OBUs of av1C, the fields of vpcC
and dOps. A box holding anything after that, which no decoder reads, is
refused. FLAC's configuration (dfLa) may hold only its stream information
and zero padding: its other blocks are tags and pictures. Other
configurations (VVC, APV, AC-4, MPEG-H, DTS...) are copied whole.
"""
from .bmff import unsupported

HIGH_PROFILES = {100, 110, 122, 144}  # H.264 profiles whose avcC may give chroma and bit depths
HEVC_FIELDS = 22   # bytes of hvcC before its arrays of parameter sets
LHEVC_FIELDS = 5   # of lhvC
AV1_FIELDS = 4     # of av1C before its configuration OBUs
AV1_OBUS = {1, 5}  # the OBU types it may hold: a sequence header, metadata
ES, DECODER, DECODER_INFO, SYNC_LAYER = 3, 4, 5, 6  # MPEG-4 descriptor tags in esds
URL_FLAG, DEPENDS_FLAG, CLOCK_FLAG = 0x40, 0x80, 0x20
STREAM_INFO, PADDING, LAST = 0, 1, 0x80  # FLAC metadata blocks


class Reader:
    """A cursor over data[position:end]."""

    def __init__(self, data, position, end):
        self.data, self.position, self.end = data, position, end

    def take(self, count):
        if self.position + count > self.end:
            raise unsupported("a truncated decoder configuration")
        start, self.position = self.position, self.position + count
        return bytes(self.data[start:self.position])

    def number(self, count):
        return int.from_bytes(self.take(count), "big")

    def items(self, count_size, mask=0xFFFF):
        """Skip a count (its bits in `mask`), then that many items of a 16-bit
        length and data."""
        for _ in range(self.number(count_size) & mask):
            self.take(self.number(2))


def check(kind, data, start, end):
    """Refuse a configuration box's payload, data[start:end], that holds more
    than its configuration."""
    reader = READERS.get(kind)
    if reader and reader(Reader(data, start, end)) != end:
        raise unsupported("data after the %s configuration" % kind.decode("latin-1"))


def avc(reader):
    reader.take(1)
    profile = reader.number(1)
    reader.take(3)  # compatibility, level, length size
    reader.items(1, 0x1F)  # sequence parameter sets, counted in the low 5 bits
    reader.items(1)  # picture parameter sets
    if reader.position < reader.end and profile in HIGH_PROFILES:
        reader.take(3)  # chroma format and bit depths
        reader.items(1)  # sequence parameter set extensions
    return reader.position


def hevc(reader, fields=HEVC_FIELDS):
    reader.take(fields)
    for _ in range(reader.number(1)):  # arrays of parameter sets, each of one type
        reader.take(1)
        reader.items(2)
    return reader.position


def av1(reader):
    reader.take(AV1_FIELDS)
    while reader.position < reader.end:
        header = reader.number(1)
        if header & 0x80 or header >> 3 & 0x0F not in AV1_OBUS:
            raise unsupported("an AV1 configuration OBU of another type")
        if header & 0x04:  # an extension byte
            reader.take(1)
        if not header & 0x02:  # no size: it runs to the end
            return reader.end
        size, shift = 0, 0
        while True:  # leb128
            byte = reader.number(1)
            size |= (byte & 0x7F) << shift
            shift += 7
            if not byte & 0x80 or shift > 56:
                break
        reader.take(size)
    return reader.position


def vp(reader):
    version = reader.number(1)
    reader.take(3 + (6 if version else 4))  # flags, then profile, level, bit depth, color...
    reader.take(reader.number(2))  # codec initialization data
    return reader.position


def opus(reader):
    reader.take(1)
    channels = reader.number(1)
    reader.take(8)  # pre-skip, input sample rate, output gain
    if reader.number(1):  # a channel mapping family other than 0: stream counts and the mapping
        reader.take(2 + channels)
    return reader.position


def flac(reader):
    if any(reader.take(4)):
        raise unsupported("the dfLa box is not in its layout")
    first = True
    while True:
        header = reader.number(1)
        kind, length = header & 0x7F, reader.number(3)
        block = reader.take(length)
        if kind != (STREAM_INFO if first else PADDING) or kind == PADDING and any(block):
            raise unsupported("FLAC metadata other than the stream information")
        first = False
        if header & LAST:
            return reader.position


def mpeg4(reader):
    if any(reader.take(4)):  # version and flags
        raise unsupported("the esds box is not in its layout")
    body = descriptor(reader, ES)
    flags = body.number(3) & 0xFF  # after the stream ID
    if flags & URL_FLAG:
        raise unsupported("an elementary stream stored elsewhere")
    body.take((2 if flags & DEPENDS_FLAG else 0) + (2 if flags & CLOCK_FLAG else 0))
    decoder = descriptor(body, DECODER)
    decoder.take(13)  # object and stream types, buffer size, bit rates
    if decoder.position < decoder.end:
        info = descriptor(decoder, DECODER_INFO)
        info.position = info.end  # the decoder's own configuration, copied whole
    finished(decoder)
    sync = descriptor(body, SYNC_LAYER)
    sync.take(1)  # a predefined sync layer configuration
    finished(sync)
    finished(body)
    return reader.position


def descriptor(reader, tag):
    """The body of the next MPEG-4 descriptor, which must be of `tag`; the
    reader moves past it."""
    if reader.number(1) != tag:
        raise unsupported("an MPEG-4 descriptor of another type")
    size = 0
    for _ in range(4):
        byte = reader.number(1)
        size = size << 7 | byte & 0x7F
        if not byte & 0x80:
            break
    start = reader.position
    reader.take(size)
    return Reader(reader.data, start, start + size)


def finished(reader):
    if reader.position != reader.end:
        raise unsupported("data after an MPEG-4 descriptor")


READERS = {b"avcC": avc, b"hvcC": hevc, b"lhvC": lambda reader: hevc(reader, LHEVC_FIELDS), b"av1C": av1,
           b"vpcC": vp, b"dOps": opus, b"dfLa": flac, b"esds": mpeg4}
