"""MP4 and QuickTime (MOV) videos, cleaned in place.

The movie box keeps what plays the video (see movie.py and MOVIE): its video
and sound tracks, with their decoder configurations, color, HDR and Dolby
Vision, spatial video, rotation and edit lists. Timed metadata tracks (such as
Apple's face and Live Photo data, GoPro's GPS and motion data and Google's
motion photo data), timecode and chapter tracks go with their samples; any
other track, subtitles for instance, is refused, and so is a sample entry
holding a box not listed here. User data and metadata boxes (location,
device, dates, software) are emptied, but for Apple's full frame rate
playback intent (see PLAYBACK_INTENT).

Top-level boxes other than ftyp, moov and mdat, such as Samsung's SEF data,
are emptied too, and dropped at the end of the file with anything else there.
Bytes in mdat that no remaining sample uses are zeroed. Media data never
moves, so every sample offset stays valid, and a video can be cleaned in a
copy of itself (see clean) without being read into memory.

Data inside the compressed samples, such as the SEI messages of H.264 and
HEVC, is copied with them, as image data is.
"""
import itertools
import struct

from .. import icc
from ..errors import FormatError
from . import bmff, movie
from .bmff import StructureError, unsupported

KEPT = {b"ftyp", b"moov", b"mdat"}
# Fragmented files keep samples outside moov; they are not supported.
REFUSED = {b"moof", b"mfra", b"sidx", b"ssix", b"styp"}
QUICKTIME = b"qt  "
# What an older QuickTime movie, without a file type box, may start with.
QUICKTIME_ATOMS = {b"moov", b"mdat", b"wide", b"free", b"skip", b"pnot"}
# Brands of MP4 files that say how to read them: ISO, MP4, iTunes (M4V, M4A)
# and 3GPP, codecs, DASH and CMAF. Other compatible brands, such as those
# naming a camera's maker, are cleared (see bmff.unknown_brands).
BRANDS = {b"isom", b"iso2", b"iso3", b"iso4", b"iso5", b"iso6", b"iso7", b"iso8", b"iso9", b"mp41", b"mp42",
          b"mp71", b"avc1", b"av01", b"iamf", b"dby1", b"M4V ", b"M4VP", b"M4VH", b"M4A ", b"M4B ", b"M4P ",
          b"3gp4", b"3gp5", b"3gp6", b"3gp7", b"3gp8", b"3gp9", b"3g2a", b"3g2b", b"3g2c", b"3ge6", b"3ge7",
          b"3gg6", b"3gr6", b"3gs6", b"3gs7", b"dash", b"cmfc", b"cmf2"}
# Brands of cameras and their formats that some write as the major brand,
# which is kept, as readers may go by it: Sony, Canon, Nikon, Panasonic,
# Casio, KDDI, Adobe's F4V.
MAKER_BRANDS = {b"XAVC", b"MSNV", b"CAEP", b"niko", b"pana", b"caqv", b"KDDI", b"mmp4", b"mqt ", b"f4v ", b"F4V "}
VIDEO_ENTRIES = {b"avc1", b"avc3", b"hvc1", b"hev1", b"dvh1", b"dvhe", b"dvav", b"dva1", b"dav1", b"av01",
                 b"vp08", b"vp09", b"mp4v", b"s263", b"h263", b"vvc1", b"vvi1", b"apv1",
                 b"apch", b"apcn", b"apcs", b"apco", b"ap4h", b"ap4x"}  # the last six: ProRes (see PRORES)
# Apple's spatial video: which views there are, their cameras, comfort and projection.
SPATIAL = {b"eyes": {b"stri": None, b"hero": None, b"cams": {b"blin": None}, b"cmfy": {b"dadj": None}},
           b"proj": {b"prji": None}, b"pack": {b"pkin": None}, b"must": None}
# Boxes of a video sample entry that say how to decode and show its samples:
# decoder configurations (Dolby Vision's among them), color and HDR, pixel
# shape and cropping, bit rates, alpha, Apple Log, and spatial video; and
# the iPod marker (uuid).
VIDEO_BOXES = dict.fromkeys({b"avcC", b"hvcC", b"lhvC", b"av1C", b"vpcC", b"vvcC", b"apvC", b"d263", b"esds",
                             b"dvcC", b"dvvC", b"dvwC", b"colr", b"pasp", b"clap", b"fiel", b"chrm", b"gama",
                             b"btrt", b"mdcv", b"clli", b"amve", b"SmDm", b"CoLL", b"hfov", b"almo", b"logs",
                             b"uuid"}) | {b"vexu": SPATIAL}
PRORES = {b"apch", b"apcn", b"apcs", b"apco", b"ap4h", b"ap4x"}
# Sound: AAC and the rest of MPEG, Apple's lossless and positional audio
# (APAC), Opus, FLAC (QuickTime's 'flac' too), Dolby, DTS, AMR, IAMF,
# QuickTime's IMA ADPCM, and uncompressed sound.
SOUND_ENTRIES = {b"mp4a", b"alac", b"apac", b"ima4", b"Opus", b"fLaC", b"flac", b"ac-3", b"ec-3", b"ac-4", b"lpcm",
                 b"ipcm", b"fpcm", b"sowt", b"twos", b"in24", b"in32", b"fl32", b"fl64", b"raw ", b"ulaw", b"alaw",
                 b"samr", b"sawb", b"mha1", b"mhm1", b"iamf", b"dtsc", b"dtse", b"dtsh", b"dtsl", b"dtsx", b"mlpa",
                 b".mp3"}
# QuickTime's sound extension: the format, its configuration, byte order, and a terminator.
WAVE = dict.fromkeys({b"frma", b"mp4a", b"esds", b"alac", b"dac3", b"dec3", b"samr", b"dfLa", b"enda", b"chan",
                      b"\0\0\0\0"})
SOUND_BOXES = dict.fromkeys({b"esds", b"chan", b"chnl", b"srat", b"dac3", b"dec3", b"dac4", b"dOps", b"alac",
                             b"dapa", b"dfLa", b"pcmC", b"damr", b"mhaC", b"mhaP", b"iacb", b"ddts", b"udts",
                             b"dmlp", b"btrt"}) | {b"wave": WAVE}
# Google's 360-degree video, version 1: a uuid box in the video track, which
# players need to show it. It is refused, as version 2's boxes are.
SPHERICAL = bytes.fromhex("ffcc8263f8554a938814587a02521fdd")
# Apple's per-frame scene illuminance, which iPhones mark as used to show their
# HDR video (a track reference 'rndr'): a metadata track with this one sample
# entry, one key in milli-lux, and samples of one 32-bit value each, which
# Apple documents as 0 to 100,000,000.
SCENE_ILLUMINANCE = bytes(6) + struct.pack(">H", 1) + bmff.box(b"keys", bmff.box(struct.pack(">I", 1), (
    bmff.box(b"keyd", b"mdta" + b"com.apple.quicktime.scene-illuminance")
    + bmff.box(b"dtyp", struct.pack(">I", 1) + b"com.apple.quicktime.milli-lux")
    + bmff.box(b"sdpd", bmff.box(b"sdpi", bytes(4)))
    + bmff.box(b"ctps", bmff.box(b"dtyp", struct.pack(">II", 0, 77))))))
ILLUMINANCE_SAMPLE = struct.pack(">II", 12, 1)  # a 12-byte item of key 1, then its value
MAX_MILLILUX = 100_000_000
# Apple's full frame rate playback intent (iOS 18, macOS 15), the one item of
# a movie's metadata kept: whether a video of 120 fps or more plays at its
# full rate (1) or in slow motion (0), as some players would otherwise play
# it. The metadata box is rewritten with it alone, as iPhones write it: an
# mdta handler, the key, and its value as a 64-bit integer.
PLAYBACK_INTENT = b"com.apple.quicktime.full-frame-rate-playback-intent"
INTEGERS = {21: True, 22: False}  # QuickTime's well-known types of big-endian integers: signed or not
METADATA_ITEMS = 256  # boxes, keys or items in one box of the metadata: iPhones write a few


def scene_illuminance(data, track):
    """Whether a track is Apple's scene illuminance exactly, as SCENE_ILLUMINANCE
    describes it, naming the tracks it helps show and nothing else."""
    if track.handler != b"meta" or [kind for kind, _ in track.references] != [b"rndr"]:
        return False
    table = movie.track_table(data, track.box)
    entries = list(bmff.boxes(data, table.stsd.content + 8, table.stsd.end))
    if len(entries) != 1 or entries[0].kind != b"mebx" or data[entries[0].content:entries[0].end] != SCENE_ILLUMINANCE:
        return False
    if any(size != len(ILLUMINANCE_SAMPLE) + 4 for size in movie.sample_sizes(data, table.stsz)):
        return False
    return all(data[position:position + 8] == ILLUMINANCE_SAMPLE
               and int.from_bytes(data[position + 8:position + 12], "big") <= MAX_MILLILUX
               for start, end in table.ranges for position in range(start, end, 12))


def playback_intent(data, meta):
    """The playback intent, 0 or 1, of a movie's metadata box that holds it as
    Apple writes one: an mdta handler, then the key once among the keys, and
    one integer item for it; else None."""
    if data[meta.content + 4:meta.content + 8] != b"hdlr":  # an ISO metadata box starts with a version
        return None
    try:
        parts = {}
        for found in listed(data, meta):
            parts.setdefault(found.kind, []).append(found)
        (hdlr,), (keys,), (ilst,) = parts.get(b"hdlr", ()), parts.get(b"keys", ()), parts.get(b"ilst", ())
        entries = listed(data, keys, 8)  # after the version, flags and count
        indices = [index for index, entry in enumerate(entries, 1)
                   if entry.kind == b"mdta" and data[entry.content:entry.end] == PLAYBACK_INTENT]
        if (data[hdlr.content + 8:hdlr.content + 12] != b"mdta" or len(indices) != 1
                or int.from_bytes(data[keys.content + 4:keys.content + 8], "big") != len(entries)):
            return None
        (item,) = [found for found in listed(data, ilst) if int.from_bytes(found.kind, "big") == indices[0]]
        (value,) = listed(data, item)
    except (StructureError, ValueError):  # none or several of a box, or too many boxes
        return None
    kind, number = int.from_bytes(data[value.content:value.content + 4], "big"), data[value.content + 8:value.end]
    if value.kind != b"data" or kind not in INTEGERS or len(number) not in (1, 2, 4, 8):
        return None
    intent = int.from_bytes(number, "big", signed=INTEGERS[kind])
    return intent if intent in (0, 1) else None


def listed(data, container, skip=0):
    """The boxes in a box of the metadata, after `skip` bytes; ValueError for
    more than METADATA_ITEMS, which a metadata box made to exhaust memory holds."""
    found = list(itertools.islice(bmff.boxes(data, container.content + skip, container.end), METADATA_ITEMS + 1))
    if len(found) > METADATA_ITEMS:
        raise ValueError("too many boxes")
    return found


def playback_metadata(intent):
    """A movie's metadata box holding only the playback intent, as iPhones write it."""
    handler = bmff.box(b"hdlr", bytes(8) + b"mdta" + bytes(14))
    keys = bmff.box(b"keys", struct.pack(">II", 0, 1) + bmff.box(b"mdta", PLAYBACK_INTENT))
    item = bmff.box(struct.pack(">I", 1), bmff.box(b"data", struct.pack(">IIq", 21, 0, intent)))
    return bmff.box(b"meta", handler + keys + bmff.box(b"ilst", item))


def rewritten_metadata(data, found):
    """A box of the movie box that goes, rewritten: its metadata box, with the playback intent alone."""
    intent = playback_intent(data, found) if found.kind == b"meta" else None
    return None if intent is None else playback_metadata(intent)


MOVIE = movie.Policy(
    boxes={
        b"moov": {b"mvhd", b"trak"},  # mvex, which only says fragments may follow, goes
        b"trak": {b"tkhd", b"tref", b"edts", b"mdia", b"tapt"},
        b"tapt": {b"clef", b"prof", b"enof"},  # QuickTime's display sizes
        b"edts": {b"elst"},
        b"mdia": {b"mdhd", b"hdlr", b"minf"},
        b"minf": {b"vmhd", b"smhd", b"gmhd", b"hdlr", b"dinf", b"stbl"},  # hdlr: QuickTime's data handler
        b"gmhd": {b"gmin"},  # the header of a scene illuminance track
        b"dinf": {b"dref"},
        b"stbl": {b"stsd", b"stts", b"ctts", b"cslg", b"stsc", b"stsz", b"stco", b"co64", b"stss", b"stps",
                  b"sdtp", b"sbgp", b"sgpd"},
    },
    entries={b"vide": (VIDEO_ENTRIES, VIDEO_BOXES), b"soun": (SOUND_ENTRIES, SOUND_BOXES),
             b"meta": ({b"mebx"}, {b"keys": None})},  # only scene illuminance: see rendering
    removed=frozenset({b"meta", b"tmcd", b"hint"}),
    chapters=True,
    # Between kept tracks: synchronization, hint, depth, parallax, auxiliary and
    # layered video, rendering. To removed ones: timecode, chapters, descriptions, hints.
    references=frozenset({b"sync", b"hind", b"vdep", b"vplx", b"auxl", b"sbas", b"scal", b"fall", b"rndr"}),
    dangling=frozenset({b"tmcd", b"chap", b"cdsc", b"hint"}),
    strict=True,
    # FFmpeg's copy of a ProRes encoder's own description, compressor name
    # included, which the ProRes decoder does not read.
    emptied=dict.fromkeys(PRORES, {b"glbl"}),
    rendering=scene_illuminance,
    rewritten={b"moov": rewritten_metadata})


def box_types(boxes):
    """The types in {box type: its own boxes, or None}, at any depth."""
    return set(boxes).union(*(box_types(children) for children in boxes.values() if children))


# Every box type a clean video may hold, which this module checks itself;
# track references are boxes too.
STRUCTURE = (KEPT | {b"free"} | movie.SELF_REFERENCES | set().union(*MOVIE.boxes.values()) | MOVIE.references
             | set().union(*(entries | box_types(boxes) for entries, boxes in MOVIE.entries.values())))


def brand_format(data):
    """"MOV", "MP4" or None, from the file type box."""
    if data[4:8] in QUICKTIME_ATOMS:
        return "MOV"
    brands = bmff.brands(data)
    if brands[:1] == [QUICKTIME]:
        return "MOV"
    return "MP4" if set(brands) & (BRANDS | MAKER_BRANDS | {QUICKTIME}) else None


def rebuild(data):
    """A cleaned copy of a video held in memory (see clean)."""
    buffer = bytearray(data)
    return bytes(buffer[:clean(data, buffer)])


def clean(original, buffer):
    """Clean `buffer`, a writable copy of `original` of the same size, in place;
    return the length of the result, which is a prefix of the buffer."""
    name = brand_format(original) or "MP4"
    try:
        length, kept = cleaned(original, buffer)
    except icc.ProfileError as error:
        raise FormatError("unsupported_profile", format=name, detail=str(error))
    except StructureError as error:
        if error.damaged:
            raise FormatError("damaged", format=name)
        raise FormatError("unsupported_part", format=name, part=str(error))
    except RecursionError:  # boxes nested beyond any real file
        raise FormatError("damaged", format=name)
    if not any(track.handler == b"vide" for track in kept):
        raise FormatError("no_video", format=name)
    return length


def cleaned(data, buffer):
    top = top_boxes(data)
    if {found.kind for found in top} & REFUSED:
        raise unsupported("fragmented video")
    movie.clear(buffer, file_type_brands(data, top[0]))
    movies = [found for found in top if found.kind == b"moov"]
    if len(movies) != 1:
        raise StructureError("no single movie box")
    if any(found.kind == b"cmov" for found in bmff.boxes(data, movies[0].content, movies[0].end)):
        raise unsupported("a compressed movie header")
    check_spherical(data, movies[0])
    kept = movie.clean(buffer, movies[0], MOVIE)
    boxes = kept_boxes(top)
    for found in top:
        if found not in boxes:
            movie.empty(buffer, found)
    media = media_spans(top)
    used = movie.movie_ranges(buffer, movies[0])
    if not movie.inside(used, media):
        raise StructureError("samples outside the media data")
    for gap in movie.uncovered(used, media):
        movie.zero(buffer, *gap)
    # Emptied boxes at the very end, and anything else there, hold no offsets anyone needs.
    return max(found.end for found in boxes), kept


def top_boxes(data):
    """The top-level boxes, up to anything after the last of them that is no box."""
    found, position = [], 0
    while len(data) - position >= 8:
        try:
            found.append(next(bmff.boxes(data, position, len(data))))
        except StructureError:
            break
        position = found[-1].end
    if not found or found[0].kind != b"ftyp" and found[0].kind not in QUICKTIME_ATOMS:
        raise StructureError("no file type box")
    return found


def kept_boxes(top):
    """The top-level boxes kept: the file type box if it comes first, the movie and the media data."""
    return {found for found in top if found.kind in KEPT and (found.kind != b"ftyp" or found is top[0])}


def file_type_brands(data, first):
    """The compatible brands cleared from the file type box, if the file
    starts with one (see bmff.unknown_brands)."""
    if first.kind != b"ftyp":
        return []  # an older QuickTime movie
    return bmff.unknown_brands(data, first, BRANDS | {QUICKTIME}, BRANDS | MAKER_BRANDS | {QUICKTIME})


def check_spherical(data, moov):
    for trak in bmff.boxes(data, moov.content, moov.end):
        if trak.kind == b"trak" and any(found.kind == b"uuid" and data[found.content:found.content + 16] == SPHERICAL
                                        for found in bmff.boxes(data, trak.content, trak.end)):
            raise unsupported("360-degree video")


def media_spans(top):
    return [(found.content, found.end) for found in top if found.kind == b"mdat"]


# ---------------------------------------------------------------- verification

def verify(original, rebuilt):
    """Check the result on its own and against the original, as listed in the
    module docstring."""
    try:
        check(original, rebuilt)
    except (StructureError, icc.ProfileError, RecursionError) as error:
        movie.fail(str(error) or type(error).__name__)


def check(original, rebuilt):
    top = list(bmff.boxes(rebuilt))  # the result is boxes to its very end
    typed = original[4:8] == b"ftyp"  # older QuickTime movies have no file type box
    if typed != (top[0].kind == b"ftyp") or typed and not movie.matches(original, rebuilt, top[0],
                                                                        file_type_brands(original, top[0])):
        movie.fail("file type box changed")
    boxes = kept_boxes(top)
    for found in top:
        if found.kind == b"free" and movie.zeroed(rebuilt, [(found.content, found.end)]):
            continue
        if found not in boxes:
            movie.fail("unexpected box %r" % found.kind)
        if rebuilt[found.start:found.content] != original[found.start:found.content]:
            movie.fail("box %r moved" % found.kind)
    movies = [found for found in top if found.kind == b"moov"]
    if len(movies) != 1:
        movie.fail("no single movie box")
    kept = movie.check(original, rebuilt, movies[0], MOVIE)
    if not any(track.handler == b"vide" for track in kept):
        movie.fail("no video track kept")
    media = media_spans(top)
    used = movie.movie_ranges(rebuilt, movies[0])
    if not movie.inside(used, media):
        movie.fail("samples outside the media data")
    if not movie.same(original, rebuilt, used):
        movie.fail("samples changed")
    if not movie.zeroed(rebuilt, movie.uncovered(used, media)):
        movie.fail("unused media data kept")
