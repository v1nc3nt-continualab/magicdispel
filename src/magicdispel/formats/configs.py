"""Decoder configurations in sample entries, read to where they end.

A decoder configuration is copied whole, as the samples are (see movie.py),
but only up to its declared end: the parameter sets of avcC, hvcC and lhvC,
the descriptors of esds, the configuration OBUs of av1C, the fields of vpcC,
dOps and dec3. A box holding anything after that, which no decoder reads, is
refused. FLAC's configuration (dfLa) may hold only its stream information
and zero padding: its other blocks are tags and pictures. Other
configurations (VVC, APV, AC-4, MPEG-H, DTS...) are copied whole. The
channel layout of ISO sound (chnl) is read the same way, its values checked.
"""
from .bmff import unsupported

BASE_PROFILES = {66, 77, 88}  # H.264 profiles whose avcC gives no chroma format and bit depths
HEVC_FIELDS = 22   # bytes of hvcC before its arrays of parameter sets
LHEVC_FIELDS = 5   # of lhvC
AV1_FIELDS = 4     # of av1C before its configuration OBUs
AV1_OBUS = {1, 5}  # the OBU types it may hold: a sequence header, metadata
ES, DECODER, DECODER_INFO, SYNC_LAYER = 3, 4, 5, 6  # MPEG-4 descriptor tags in esds
URL_FLAG, DEPENDS_FLAG, CLOCK_FLAG = 0x40, 0x80, 0x20
STREAM_INFO, PADDING, LAST = 0, 1, 0x80  # FLAC metadata blocks
STREAM_INFO_SIZE = 34
CHANNELS_STRUCTURED, OBJECTS_STRUCTURED = 1, 2  # chnl's stream structure
EXPLICIT_POSITION, SPEAKER_POSITIONS, LAYOUTS = 126, 64, 64  # chnl: a speaker given by angles; the codes defined
ITEMS = 256  # parameter sets, OBUs or metadata blocks in one configuration: real ones hold a few


class Reader:
    """A cursor over data[position:end]."""

    def __init__(self, data, position, end):
        self.data, self.position, self.end, self.count = data, position, end, 0

    def item(self):
        """Count one more item of a list, of which a configuration holds only a few."""
        self.count += 1
        if self.count > ITEMS:
            raise unsupported("a decoder configuration of too many parts")

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
            self.item()
            self.take(self.number(2))


def check(kind, data, start, end, channels=None):
    """Refuse a configuration box's payload, data[start:end], that holds more
    than its configuration; `channels` are those of the sound it is in."""
    reader = READERS.get(kind)
    if reader and (channel_layout(Reader(data, start, end), channels) if reader is channel_layout
                   else reader(Reader(data, start, end))) != end:
        raise unsupported("data after the %s configuration" % kind.decode("latin-1"))


def avc(reader):
    reader.take(1)
    profile = reader.number(1)
    reader.take(3)  # compatibility, level, length size
    reader.items(1, 0x1F)  # sequence parameter sets, counted in the low 5 bits
    reader.items(1)  # picture parameter sets
    if reader.position < reader.end and profile not in BASE_PROFILES:  # as FFmpeg and 14496-15 write it
        reader.take(3)  # chroma format and bit depths
        reader.items(1)  # sequence parameter set extensions
    return reader.position


def hevc(reader, fields=HEVC_FIELDS):
    reader.take(fields)
    for _ in range(reader.number(1)):  # arrays of parameter sets, each of one type
        reader.item()
        reader.take(1)
        reader.items(2)
    return reader.position


def av1(reader):
    reader.take(AV1_FIELDS)
    while reader.position < reader.end:
        reader.item()
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
    version, flags = reader.number(1), reader.number(3)
    if version not in (0, 1) or flags:
        raise unsupported("the vpcC box is not in its layout")
    reader.take(6 if version else 4)  # profile, level, bit depth, color...
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
        reader.item()
        header = reader.number(1)
        kind, length = header & 0x7F, reader.number(3)
        block = reader.take(length)
        if kind != (STREAM_INFO if first else PADDING) or first and length != STREAM_INFO_SIZE or \
                kind == PADDING and any(block):
            raise unsupported("FLAC metadata other than the stream information")
        first = False
        if header & LAST:
            return reader.position


def eac3(reader):
    reader.take(1)
    streams = (reader.number(1) & 0x07) + 1  # after the data rate: independent substreams
    for _ in range(streams):
        reader.take(2)
        if reader.number(1) >> 1 & 0x0F:  # dependent substreams: then their channel locations
            reader.take(1)
    if reader.end - reader.position == 2:  # Dolby Atmos: the extension type A flag and its complexity
        reader.take(2)
    return reader.position


def channel_layout(reader, channels):
    """ISO's channel layout (chnl), versions 0 and 1, of sound of `channels`:
    a speaker position for each of them, or a defined layout that leaves
    none out, and objects."""
    version, flags = reader.number(1), reader.number(3)
    if version not in (0, 1) or flags or not channels:
        raise unsupported("the chnl box is not in its layout")
    structure = reader.number(1)
    if version:
        structure, ordering = structure >> 4, structure & 0x0F
        if ordering > 1 or reader.number(1) != channels:  # the format ordering, the base channel count
            raise unsupported("the chnl box is not in its layout")
    if structure & ~(CHANNELS_STRUCTURED | OBJECTS_STRUCTURED):
        raise unsupported("the chnl box is not in its layout")
    if structure & CHANNELS_STRUCTURED:
        layout = reader.number(1)
        if layout >= LAYOUTS:
            raise unsupported("a channel layout of no known meaning")
        if layout == 0:
            count = reader.number(1) if version else channels
            if count != channels and not structure & OBJECTS_STRUCTURED or count > channels:
                raise unsupported("the chnl box is not in its layout")
            for _ in range(count):
                speaker(reader)
        elif version == 0 and any(reader.take(8)):  # the channels left out: none
            raise unsupported("a channel layout that leaves channels out")
        elif version and reader.number(1):  # reserved bits, the channel order, whether channels are left out
            raise unsupported("a channel layout that leaves channels out")
    if structure & OBJECTS_STRUCTURED and not version:
        reader.take(1)  # the object count
    return reader.position


def speaker(reader):
    """A speaker position: a code, or angles."""
    position = reader.number(1)
    if position == EXPLICIT_POSITION:
        azimuth = int.from_bytes(reader.take(2), "big", signed=True)
        elevation = int.from_bytes(reader.take(1), "big", signed=True)
        if not (-180 <= azimuth <= 180 and -90 <= elevation <= 90):
            raise unsupported("a speaker position of no meaning")
    elif position >= SPEAKER_POSITIONS:
        raise unsupported("a speaker position of no meaning")


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
           b"vpcC": vp, b"dOps": opus, b"dfLa": flac, b"esds": mpeg4, b"dec3": eac3, b"chnl": channel_layout}
