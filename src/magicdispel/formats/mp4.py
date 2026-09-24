"""MP4 and QuickTime (MOV) videos, cleaned in place.

The movie box keeps what plays the video (see movie.py and MOVIE): its video
and sound tracks, with their decoder configurations, color, HDR and Dolby
Vision, spatial video, rotation and edit lists. Timed metadata tracks (such as
Apple's face and Live Photo data, GoPro's GPS and motion data and Google's
motion photo data), timecode and chapter tracks go with their samples; any
other track, subtitles for instance, is refused, and so is a sample entry
holding a box not listed here. User data and metadata boxes (location,
device, dates, software) are emptied.

Top-level boxes other than ftyp, moov and mdat, such as Samsung's SEF data,
are emptied too, and dropped at the end of the file with anything else there.
Bytes in mdat that no remaining sample uses are zeroed. Media data never
moves, so every sample offset stays valid, and a video can be cleaned in a
copy of itself (see clean) without being read into memory.

Data inside the compressed samples, such as the SEI messages of H.264 and
HEVC, is copied with them, as image data is.
"""
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
# Brands of MP4 files: ISO, MP4, M4V and 3GPP, and those cameras and phones write.
BRANDS = {b"isom", b"iso2", b"iso3", b"iso4", b"iso5", b"iso6", b"iso7", b"iso8", b"iso9", b"mp41", b"mp42",
          b"mp71", b"avc1", b"M4V ", b"M4VP", b"M4VH", b"3gp4", b"3gp5", b"3gp6", b"3gp7", b"3gp8", b"3gp9",
          b"3g2a", b"3g2b", b"3g2c", b"3ge6", b"3ge7", b"3gg6", b"MSNV", b"XAVC", b"CAEP", b"dby1"}
VIDEO_ENTRIES = {b"avc1", b"avc3", b"hvc1", b"hev1", b"dvh1", b"dvhe", b"dvav", b"dva1", b"dav1", b"av01",
                 b"vp08", b"vp09", b"mp4v", b"s263", b"h263", b"vvc1", b"vvi1", b"apv1",
                 b"apch", b"apcn", b"apcs", b"apco", b"ap4h", b"ap4x"}  # the last six: ProRes
# Apple's spatial video: which views there are, their cameras, comfort and projection.
SPATIAL = {b"eyes": {b"stri": None, b"hero": None, b"cams": {b"blin": None}, b"cmfy": {b"dadj": None}},
           b"proj": {b"prji": None}, b"pack": {b"pkin": None}, b"must": None}
# Boxes of a video sample entry that say how to decode and show its samples:
# decoder configurations (Dolby Vision's among them), color and HDR, pixel
# shape and cropping, bit rates, and spatial video.
VIDEO_BOXES = dict.fromkeys({b"avcC", b"hvcC", b"lhvC", b"av1C", b"vpcC", b"vvcC", b"apvC", b"d263", b"esds",
                             b"dvcC", b"dvvC", b"dvwC", b"colr", b"pasp", b"clap", b"fiel", b"gama", b"btrt",
                             b"mdcv", b"clli", b"cclv", b"amve", b"SmDm", b"CoLL", b"hfov"}) | {b"vexu": SPATIAL}
SOUND_ENTRIES = {b"mp4a", b"alac", b"Opus", b"fLaC", b"ac-3", b"ec-3", b"ac-4", b"lpcm", b"ipcm", b"fpcm",
                 b"sowt", b"twos", b"in24", b"in32", b"fl32", b"fl64", b"raw ", b"ulaw", b"alaw", b"samr",
                 b"sawb", b"mha1", b"mhm1", b"iamf", b"dtsc", b"dtse", b"dtsh", b"dtsl", b"dtsx", b"mlpa",
                 b".mp3"}
# QuickTime's sound extension: the format, its configuration, byte order, and a terminator.
WAVE = dict.fromkeys({b"frma", b"mp4a", b"esds", b"alac", b"enda", b"chan", b"\0\0\0\0"})
SOUND_BOXES = dict.fromkeys({b"esds", b"chan", b"chnl", b"srat", b"dac3", b"dec3", b"dac4", b"dOps", b"alac",
                             b"dfLa", b"pcmC", b"damr", b"mhaC", b"mhaP", b"iacb", b"ddts", b"udts", b"dmlp",
                             b"SA3D", b"btrt"}) | {b"wave": WAVE}
MOVIE = movie.Policy(
    boxes={
        b"moov": {b"mvhd", b"trak", b"mvex"},
        b"mvex": {b"mehd", b"trex"},
        b"trak": {b"tkhd", b"tref", b"edts", b"mdia", b"tapt"},
        b"tapt": {b"clef", b"prof", b"enof"},  # QuickTime's display sizes
        b"edts": {b"elst"},
        b"mdia": {b"mdhd", b"hdlr", b"minf"},
        b"minf": {b"vmhd", b"smhd", b"hdlr", b"dinf", b"stbl"},  # hdlr: QuickTime's data handler
        b"dinf": {b"dref"},
        b"stbl": {b"stsd", b"stts", b"ctts", b"cslg", b"stsc", b"stsz", b"stco", b"co64", b"stss", b"stps",
                  b"stsh", b"sdtp", b"sbgp", b"sgpd", b"subs", b"padb"},
    },
    entries={b"vide": (VIDEO_ENTRIES, VIDEO_BOXES), b"soun": (SOUND_ENTRIES, SOUND_BOXES)},
    removed=frozenset({b"meta", b"tmcd", b"hint"}),
    chapters=True,
    # Between kept tracks: synchronization, hint, depth, parallax, auxiliary and
    # layered video. To removed ones: timecode, chapters, descriptions, hints.
    references=frozenset({b"sync", b"hind", b"vdep", b"vplx", b"auxl", b"sbas", b"scal", b"fall"}),
    dangling=frozenset({b"tmcd", b"chap", b"cdsc", b"hint"}),
    strict=True)


def box_types(boxes):
    """The types in {box type: its own boxes, or None}, at any depth."""
    return set(boxes).union(*(box_types(children) for children in boxes.values() if children))


# Every box type a clean video may hold, which this module checks itself.
STRUCTURE = (KEPT | {b"free"} | movie.SELF_REFERENCES | set().union(*MOVIE.boxes.values())
             | set().union(*(entries | box_types(boxes) for entries, boxes in MOVIE.entries.values())))


def brand_format(data):
    """"MOV", "MP4" or None, from the file type box."""
    if data[4:8] in QUICKTIME_ATOMS:
        return "MOV"
    if data[4:8] != b"ftyp" or len(data) < 16:
        return None
    size = int.from_bytes(data[:4], "big")
    brands = {data[8:12]} | {data[n:n + 4] for n in range(16, min(size, len(data)) - 3, 4)}
    if data[8:12] == QUICKTIME:
        return "MOV"
    return "MP4" if brands & BRANDS or QUICKTIME in brands else None


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
    movies = [found for found in top if found.kind == b"moov"]
    if len(movies) != 1:
        raise StructureError("no single movie box")
    kept = movie.clean(buffer, movies[0], MOVIE)
    for found in top:
        if found.kind not in KEPT:
            movie.empty(buffer, found)
    media = media_spans(top)
    used = movie.movie_ranges(buffer, movies[0])
    if not inside(used, media):
        raise StructureError("samples outside the media data")
    for start, end in media:
        for gap in movie.gaps(used, start, end):
            movie.zero(buffer, *gap)
    # Emptied boxes at the very end, and anything else there, hold no offsets anyone needs.
    return max(found.end for found in top if found.kind in KEPT), kept


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


def media_spans(top):
    return [(found.content, found.end) for found in top if found.kind == b"mdat"]


def inside(used, media):
    """Whether every sample lies in a media data box."""
    return all(start == end or any(a <= start and end <= b for a, b in media) for start, end in used)


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
    if typed != (top[0].kind == b"ftyp") or typed and rebuilt[:top[0].end] != original[:top[0].end]:
        movie.fail("file type box changed")
    for found in top:
        if found.kind == b"free" and movie.zeroed(rebuilt, [(found.content, found.end)]):
            continue
        if found.kind not in KEPT:
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
    if not inside(used, media):
        movie.fail("samples outside the media data")
    if not movie.same(original, rebuilt, used):
        movie.fail("samples changed")
    if not movie.zeroed(rebuilt, [gap for start, end in media for gap in movie.gaps(used, start, end)]):
        movie.fail("unused media data kept")
