"""The movie box (moov) of ISO base media and QuickTime files, cleaned in place.

A movie keeps only the boxes that play it, from a fixed list per container
(Policy.boxes): headers, tracks and their edits, and the sample tables.
Readers skip boxes they do not know, so any other box becomes a zero-filled
free box of the same size, and nothing moves; a box that belongs in another
part of the movie means the file is damaged. A track is kept, removed or
refused by its handler type. A removed track is emptied, and its samples,
which no remaining table points to, are zeroed with the rest of the unused
media (see uncovered). A kept track keeps sample entries of known types, and
in each only the boxes that say how to decode and show its samples.
Creation and modification times, handler and compressor names, vendor codes
and data reference locations are cleared.

Every kept box has exactly the layout its type and version give it, with no
box type twice where the standard allows one, and a kept track's sample
tables must agree with each other (see Table), so that players read the
samples this module keeps, and nothing else can ride along. Decoder
configurations (such as avcC and hvcC) are copied whole, as the samples are.
"""
import bisect
import itertools
import math
import struct
from collections import Counter
from dataclasses import dataclass
from typing import NamedTuple

from .. import icc
from ..errors import VerificationError
from . import bmff, configs
from .bmff import StructureError, unsupported

TIMED = {b"mvhd", b"tkhd", b"mdhd"}
HANDLER_FIELDS = 12        # a handler's version, flags and types; its vendor, flags and name follow
VISUAL_FIELDS = 78         # bytes of a visual sample entry before its boxes
SOUND_FIELDS = 28          # of a sound sample entry; QuickTime's versions 1 and 2 add more
SAMPLE_FIELDS = 8          # of any other: reserved bytes and a data reference index
QUICKTIME_SOUND = {1: 16, 2: 36}
# QuickTime's sound entry of version 2: the fields that hold constants, and the size of its fields.
V2_CONSTANTS, V2_MARK = struct.pack(">HHhHII", 3, 16, -2, 0, 0x10000, 72), struct.pack(">I", 0x7F000000)
# Bounds of its other fields: the sample rate, lpcm's format flags (float,
# big-endian, signed, packed), other types' (such as ALAC's source bits), the
# bytes and frames of a packet.
MAX_RATE, LPCM_FLAGS, MAX_FLAGS, MAX_PACKET = 1_000_000, 0x0F, 4, 1 << 16
VISUAL_HANDLERS = {b"pict", b"vide", b"auxv"}
VENDOR = (12, 16)          # within a visual or sound entry: QuickTime's vendor code
DATA_SIZE = (36, 40)       # within a visual entry: reserved, or QuickTime's data size, always 0
COMPRESSOR_NAME = (42, 74)  # within a visual entry
# A handler's component type: none (ISO), QuickTime's media handler, or its
# data handler, with the kinds of data reference it may name.
COMPONENTS = {b"\0\0\0\0": None, b"mhlr": None, b"dhlr": {b"alis", b"url ", b"rsrc"}}
SAMPLE_RESERVED = 6         # the reserved bytes every sample entry starts with, cleared
# Fields no reader uses, cleared with the times: reserved ones, and in mvhd
# QuickTime's preview, poster, selection and current times. (start, end) in
# the payload, by version 0 and 1.
UNUSED = {b"mvhd": ([(26, 36), (72, 96)], [(38, 48), (84, 108)]),
          b"tkhd": ([(16, 20), (24, 32), (38, 40)], [(24, 28), (36, 44), (50, 52)]),
          b"mdhd": ([(22, 24)], [(34, 36)]),  # ISO's pre_defined, QuickTime's quality
          b"smhd": ([(6, 8)], [(6, 8)]),
          b"gmin": ([(14, 16)], [(14, 16)])}
# Payload sizes of fixed-size boxes, by version where they have versions 0 and 1.
FIXED_SIZES = {b"mvhd": (100, 112), b"tkhd": (84, 96), b"mdhd": (24, 36), b"cslg": (24, 44), b"mehd": (8, 12),
               b"vmhd": 12, b"smhd": 8, b"nmhd": 4, b"gmin": 16, b"trex": 24, b"clef": 12, b"prof": 12, b"enof": 12}
# Tables of a count and that many entries: (bytes before the count, entry size by version).
TABLES = {b"stts": (4, (8, 8)), b"ctts": (4, (8, 8)), b"stss": (4, (4, 4)), b"stps": (4, (4, 4)),
          b"stsc": (4, (12, 12)), b"stco": (4, (4, 4)), b"co64": (4, (8, 8)), b"elst": (4, (12, 20))}
TABLE_VERSIONS = {b"ctts": (0, 1), b"elst": (0, 1)}  # the others have only version 0
TABLE_FLAGS = {b"elst": (0, 1)}  # an edit list's flag 1: its edits repeat, as animations loop
# Boxes that belong in one place of a movie; one found anywhere else means a damaged file.
PLACED = {b"trak", b"tkhd", b"edts", b"elst", b"mdia", b"mdhd", b"minf", b"stbl", b"stsd", b"stts", b"ctts",
          b"stsc", b"stsz", b"stco", b"co64", b"stss"}
REPEATABLE = {b"trak", b"sgpd", b"sbgp"}  # the others appear once in their container
# The boxes of a sample table that track_table reads.
TABLE_PARTS = {b"stsd", b"stts", b"ctts", b"stsc", b"stsz", b"stz2", b"stco", b"co64", b"stss", b"stps", b"sdtp",
               b"sgpd", b"sbgp"}
# sdtp bytes with a reserved value (3) in whether a sample depends on others,
# others on it, or it is coded redundantly.
SDTP_RESERVED = bytes(3 in (value >> 4 & 3, value >> 2 & 3, value & 3) for value in range(256))
# Sample groups kept, by grouping type: (the size of each description, its
# reserved bits in the first byte). Roll and pre-roll distances, sync and
# random access points, temporal levels and layers. Other groups only help
# decoders seek, and are emptied.
GROUPINGS = {b"roll": (2, 0), b"prol": (2, 0), b"sync": (1, 0xC0), b"rap ": (1, 0), b"sap ": (1, 0x70),
             b"tele": (1, 0x7F), b"tscl": (20, 0)}
# Fixed-size boxes in sample entries. colr is checked by its type: nclx, QuickTime's
# nclc, or an ICC profile.
ENTRY_SIZES = {b"pasp": 8, b"clap": 32, b"fiel": 2, b"clli": 4, b"mdcv": 24, b"amve": 8, b"btrt": 12,
               b"gama": 4, b"dvcC": 24, b"dvvC": 24, b"dvwC": 24, b"hfov": 4, b"frma": 4, b"enda": 2,
               b"mp4a": 4, b"\0\0\0\0": 0, b"dac3": 3, b"damr": 9, b"samr": 9, b"d263": 7, b"pcmC": 6, b"srat": 8,
               b"SmDm": 28, b"CoLL": 8,  # VP9's mastering display and light levels
               b"chrm": 2, b"ccst": 8,  # the chroma location of each field; coding constraints
               # spatial video: stereo views, hero eye, baseline, disparity, projection, packing
               b"stri": 5, b"blin": 8, b"dadj": 8, b"prji": 8, b"pkin": 8}
# Sample entry boxes that are full boxes of version 0 and no flags.
FULL_BOXES = {b"stri", b"blin", b"dadj", b"prji", b"pkin", b"srat", b"pcmC", b"SmDm", b"CoLL", b"ccst", b"chan",
              b"must"}
# Sample entry boxes kept only with a payload their writers are known to give
# them: Apple's alpha channel modes and log encoding, and the iPod marker
# box that FFmpeg (0) and mp4v2 (1) write into H.264 entries.
IPOD = bytes.fromhex("6b6840f25f244fc5ba39a51bcf0323f3")
KNOWN_PAYLOADS = {b"almo": {bytes.fromhex("00000100"), bytes.fromhex("00000102")},
                  b"logs": {b"com.apple.rec2020.apple-log"},
                  b"uuid": {IPOD + bytes(4), IPOD + (1).to_bytes(4, "big")},
                  # in QuickTime's sound extension: byte order, and the AAC marker
                  b"enda": {bytes(2), (1).to_bytes(2, "big")}, b"mp4a": {bytes(4)},
                  b"hero": {bytes(4) + bytes([eye]) for eye in (0, 1, 2)}}  # none, left or right
# Kinds a spatial video box names after its version and flags: projections, packings.
KINDS = {b"prji": {b"rect", b"equi", b"hequ", b"fish", b"prim"}, b"pkin": {b"side", b"over"}}
# (offset, reserved bits) of the byte after the version and flags: pcmC's
# format flags but little-endian, stri's reserved bits before its eyes.
RESERVED = {b"pcmC": (4, 0xFE), b"stri": (4, 0xF0)}
CHROMA_LOCATIONS = 6  # the largest chroma location code
CODING_RESERVED = 0x03FFFFFF  # ccst: the bits after its intra-coding flags and reference count
ALPHA = {b"urn:mpeg:mpegB:cicp:systems:auxiliary:alpha", b"urn:mpeg:hevc:2015:auxid:1"}  # auxi types kept
# A QuickTime channel layout (chan): its tags that use a channel bitmap, or
# descriptions of the channels, and the most channels described.
USE_DESCRIPTIONS, USE_BITMAP, UNKNOWN_LAYOUT, CHANNELS = 0, 0x10000, 0xFFFF, 24
CHANNEL_BITS = (1 << 18) - 1  # the channels a layout bitmap may name
# Channel labels besides those up to 255: headphones, click track, foreign
# language, a discrete channel, and a numbered one (discrete, ambisonic, object).
LABELS = {301, 302, 304, 305, 400, 500}
NUMBERED_LABELS = range(1, 5)  # label >> 16
COLOR_SIZES = {b"nclx": (11, 10), b"nclc": (10,)}  # some Android phones leave out nclx's range byte
PROFILE_COLORS = {b"prof", b"rICC"}  # ICC profiles, sanitized
DOLBY_VISION = {b"dvcC", b"dvvC", b"dvwC"}
# Leading bytes of 3GPP decoder boxes that name the codec's maker, cleared.
ENTRY_VENDORS = {b"d263": 4, b"damr": 4, b"samr": 4}  # samr: AMR's configuration in QuickTime's wave
# Data references that mean "in this file" when their flag 1 is set.
SELF_REFERENCES = {b"url ", b"urn ", b"alis"}
# Uncompressed QuickTime sound: the bits per sample of its type, or those
# the sample entry may give, where FFmpeg reads them as AVFoundation does
# (FFmpeg reads 'raw ' of any other size as 8 bits, twos and sowt as 16).
PCM_BITS = {b"in24": 24, b"in32": 32, b"fl32": 32, b"fl64": 64, b"ulaw": 8, b"alaw": 8}
ENTRY_BITS = {b"twos": {8, 16, 24, 32}, b"sowt": {8, 16, 24, 32}, b"raw ": {8, 16}}
ISO_PCM = {b"ipcm", b"fpcm"}  # ISO's uncompressed sound, whose bits per sample are in its pcmC box
LPCM = b"lpcm"  # QuickTime's, described by a version 2 entry
LPCM_FLOAT, LPCM_SIGNED = 1, 4  # its format flags
SAMPLE_BITS = {8, 16, 24, 32, 64}
PCM = set(PCM_BITS) | set(ENTRY_BITS) | ISO_PCM | {LPCM}
# Compressed sound that FFmpeg and AVFoundation read in packets of their own,
# whatever the entry says: (bytes a channel, frames) of IMA ADPCM's.
PACKED = {b"ima4": (34, 64)}
CHUNK = 1 << 20
BLOCK = 1 << 16  # table entries read at a time


@dataclass(frozen=True)
class Policy:
    """What a movie keeps.

    boxes: {container: types of the boxes it keeps}; kept boxes that are keys
        themselves are cleaned in turn.
    entries: {handler type of a kept track: (sample entry types, {box type in
        such an entry: its own kept boxes, or None for a box without any})}.
    removed: handler types of tracks removed with their samples. A track whose
        handler type is neither kept nor removed is refused.
    chapters: whether a text track that a kept track names as its chapters goes too.
    thumbnails: whether a track that is a thumbnail of another ('thmb') goes too.
    references: track reference types kept between kept tracks; None keeps
        all of them as they are.
    dangling: reference types from a kept track that go with the removed
        tracks they point to.
    strict: refuse sample entries holding boxes the policy does not list,
        rather than emptying those boxes.
    emptied: {sample entry type: types of boxes in it that are emptied even
        so, as no player needs them and they hold what none should see}.
    rendering: a function (data, track) telling whether a track of a removed
        handler type is one that another track is shown with ('rndr'), and
        that the policy recognizes exactly; such a track is kept. A removed
        track that another is shown with is refused otherwise.
    """
    boxes: dict
    entries: dict
    removed: frozenset = frozenset()
    chapters: bool = False
    thumbnails: bool = False
    references: frozenset = None
    dangling: frozenset = frozenset()
    strict: bool = False
    emptied: dict = None
    rendering: object = None


class Track(NamedTuple):
    box: bmff.Box
    ident: int
    handler: bytes
    references: list      # [(reference type, [track IDs])]


class Table(NamedTuple):
    """A track's sample table, checked (see track_table)."""
    ranges: list          # (start, end) of each chunk of samples, in order
    count: int            # samples
    stsd: bmff.Box
    stsz: bmff.Box
    spare: list           # (start, end) of sample group descriptions no sample uses, cleared


# ----------------------------------------------------------------------- tracks

def tracks(data, moov):
    found = [Track(trak, track_id(data, trak), handler(data, trak), track_references(data, trak))
             for trak in bmff.boxes(data, moov.content, moov.end) if trak.kind == b"trak"]
    if len({track.ident for track in found}) != len(found):
        raise StructureError("duplicate track ID")
    return found


def track_id(data, trak):
    for found in bmff.boxes(data, trak.content, trak.end):
        if found.kind == b"tkhd":
            if found.end - found.content < 4:
                raise StructureError("truncated track header")
            offset = found.content + (20 if data[found.content] == 1 else 12)  # after the times
            if offset + 4 > found.end:
                raise StructureError("truncated track header")
            return int.from_bytes(data[offset:offset + 4], "big")
    raise StructureError("track without a header")


def handler(data, trak):
    """The handler type of a track: vide, soun, meta, pict..."""
    for media in bmff.boxes(data, trak.content, trak.end):
        if media.kind == b"mdia":
            for found in bmff.boxes(data, media.content, media.end):
                if found.kind == b"hdlr" and found.end - found.content >= 12:
                    return bytes(data[found.content + 8:found.content + 12])
    raise StructureError("track without a handler")


def track_references(data, trak):
    """[(type, [track IDs])] of a track's references; free boxes are no references."""
    references = []
    for found in bmff.boxes(data, trak.content, trak.end):
        if found.kind == b"tref":
            for reference in bmff.boxes(data, found.content, found.end):
                if reference.kind == b"free":
                    continue
                if (reference.end - reference.content) % 4:
                    raise StructureError("invalid track reference")
                references.append((reference.kind, [int.from_bytes(data[p:p + 4], "big")
                                                    for p in range(reference.content, reference.end, 4)]))
    return references


def chapter_images(data, track, others):
    """Whether a track that a kept track names as its chapters is their
    pictures: a disabled video track nothing else refers to."""
    tkhd = next(found for found in bmff.boxes(data, track.box.content, track.box.end) if found.kind == b"tkhd")
    return track.handler == b"vide" and track.ident not in others and not data[tkhd.content + 3] & 1  # enabled


def removed_tracks(data, found, policy):
    """The IDs of the tracks that go. Any other track the policy does not keep
    is refused, and so is a reference that removing them would break."""
    chapters = {ident for track in found for kind, idents in track.references if kind == b"chap" for ident in idents}
    others = {ident for track in found for kind, idents in track.references if kind != b"chap" for ident in idents}
    removed = {track.ident for track in found
               if (track.handler in policy.removed or policy.chapters and track.ident in chapters and (
                   track.handler == b"text" or chapter_images(data, track, others)))
               and not (policy.rendering and policy.rendering(data, track))}
    if policy.thumbnails:  # a thumbnail of tracks that stay
        staying = {track.ident for track in found} - removed
        removed |= {track.ident for track in found
                    if any(kind == b"thmb" for kind, _ in track.references)
                    and all(idents and set(idents) <= staying - {track.ident}
                            for kind, idents in track.references if kind == b"thmb")}
    for track in found:
        if track.ident not in removed and track.handler not in policy.entries:
            raise unsupported("%s track" % track.handler.decode("latin-1"))
    for track in found:
        for kind, idents in track.references:
            if track.ident in removed:
                # A removed track may describe others, but none may need it to be shown.
                if kind == b"rndr" and set(idents) - removed:
                    raise unsupported("a track needed to show another")
            elif set(idents) & removed:
                if kind not in policy.dangling or not set(idents) <= removed:
                    raise unsupported("a kept track depends on a removed one")
            elif policy.references is not None and kind not in policy.references:
                raise unsupported("track reference " + kind.decode("latin-1"))
    return removed


# --------------------------------------------------------------------- cleaning

def kept_ranges(data, moov, policy):
    """(start, end) of the chunks of the tracks that clean keeps."""
    found = tracks(data, moov)
    removed = removed_tracks(data, found, policy)
    return [span for track in found if track.ident not in removed for span in track_table(data, track.box).ranges]


def clean(buffer, moov, policy):
    """Clean a movie box in place, as the policy says; [Track] of those kept."""
    found = tracks(buffer, moov)
    removed = removed_tracks(buffer, found, policy)
    spare = []
    for track in found:
        if track.ident in removed:
            empty(buffer, track.box)
        else:
            spare += track_table(buffer, track.box).spare
    clean_container(buffer, moov, policy, None, removed)
    clear(buffer, spare)
    return [track for track in found if track.ident not in removed]


def clean_container(buffer, container, policy, track_handler, removed):
    children = list(bmff.boxes(buffer, container.content, container.end))
    allowed = policy.boxes[container.kind]
    check_repeats(buffer, children, allowed)
    groups = kept_groupings(buffer, children) if container.kind == b"stbl" else set()
    for found in children:
        if found.kind not in allowed:
            if found.kind in PLACED:
                raise StructureError("misplaced %s box" % found.kind.decode("latin-1"))
            empty(buffer, found)
        elif found.kind in (b"sgpd", b"sbgp") and grouping(buffer, found) not in groups:
            empty(buffer, found)  # a seeking hint this module cannot check
        elif found.kind in policy.boxes:
            clean_container(buffer, found, policy, handler(buffer, found) if found.kind == b"trak" else track_handler,
                            removed)
        elif found.kind == b"tref":
            for reference in bmff.boxes(buffer, found.content, found.end):
                targets = {int.from_bytes(buffer[p:p + 4], "big") for p in range(reference.content, reference.end, 4)}
                if reference.kind == b"free" or targets <= removed:  # see removed_tracks: only dangling types
                    empty(buffer, reference)
        elif found.kind == b"stsd":
            clean_entries(buffer, found, track_handler, policy)
        else:
            check_layout(buffer, found)
            clear(buffer, cleared_fields(buffer, found))


def check_repeats(data, children, allowed):
    """Refuse a kept box type found twice in one container, where readers would
    take one of them and this module maybe the other."""
    counts = Counter(found.kind for found in children if found.kind in allowed)
    repeated = [kind for kind, count in counts.items() if count > 1 and kind not in REPEATABLE]
    if counts[b"stco"] and counts[b"co64"]:
        repeated.append(b"co64")
    groups = Counter((found.kind, grouping(data, found)) for found in children if found.kind in (b"sgpd", b"sbgp"))
    repeated += [kind for (kind, _), count in groups.items() if count > 1]
    if repeated:
        raise StructureError("repeated %s box" % repeated[0].decode("latin-1"))


def grouping(data, found):
    """The grouping type of a sample group box (sgpd or sbgp)."""
    if found.end - found.content < 8:
        raise StructureError("truncated sample group")
    return bytes(data[found.content + 4:found.content + 8])


def kept_groupings(data, children):
    """The grouping types of an stbl's sample groups that are kept: those whose
    descriptions this module can check."""
    return {grouping(data, found) for found in children
            if found.kind == b"sgpd" and grouping(data, found) in GROUPINGS}


def clean_entries(buffer, stsd, track_handler, policy):
    """Keep in each sample entry only the boxes the policy lists for its track."""
    kept = policy.entries[track_handler][1]
    for entry, fields in sample_entries(buffer, stsd, track_handler, policy):
        clear(buffer, entry_fields(entry, track_handler))
        children = entry_boxes(buffer, entry, fields)
        check_entry_repeats(buffer, children, kept)
        for child in children:
            if child.kind in kept:
                check_entry_box(buffer, child, kept[child.kind], kept, entry)
                clear(buffer, entry_box_fields(buffer, child, kept[child.kind]))
            elif child.kind == b"free" and zeroed(buffer, [(child.content, child.end)]):
                continue  # holds nothing: as this module leaves an emptied box, cleaned again
            elif child.kind in (policy.emptied or {}).get(entry.kind, ()):
                empty(buffer, child)
            elif policy.strict:
                raise unsupported("%s box in a %s sample entry" % (child.kind.decode("latin-1"),
                                                                  entry.kind.decode("latin-1")))
            else:
                empty(buffer, child)
        for start, end in profiles_in(buffer, entry, fields):
            buffer[start:end] = icc.sanitize(bytes(buffer[start:end]))


def sample_entries(data, stsd, track_handler, policy):
    """[(entry, bytes of fields before its boxes)] of a sample description,
    whose entries must be of types the policy knows for the track."""
    if stsd.end - stsd.content < 8:
        raise StructureError("truncated sample description")
    version = data[stsd.content]
    types = policy.entries[track_handler][0]
    found = []
    for entry in bmff.boxes(data, stsd.content + 8, stsd.end):
        if entry.kind not in types:
            raise unsupported("sample entry " + entry.kind.decode("latin-1"))
        fields = (VISUAL_FIELDS if track_handler in VISUAL_HANDLERS
                  else SOUND_FIELDS if track_handler == b"soun" else SAMPLE_FIELDS)
        if fields == SOUND_FIELDS and entry.end - entry.content >= fields and version == 0:
            # QuickTime sound entries of versions 1 and 2 have more fields. In ISO files a
            # version 1 entry, of the same size as version 0, only follows a version 1
            # sample description.
            entry_version = int.from_bytes(data[entry.content + 8:entry.content + 10], "big")
            if entry_version not in (0, 1, 2):
                raise unsupported("sound sample entry version")
            fields += QUICKTIME_SOUND.get(entry_version, 0)
        if entry.end - entry.content < fields:
            raise StructureError("truncated sample entry")
        if fields >= SOUND_FIELDS and track_handler == b"soun":
            check_sound_constants(data, entry, fields)
        found.append((entry, fields))
    if len(found) != int.from_bytes(data[stsd.content + 4:stsd.content + 8], "big"):
        raise StructureError("invalid sample entry count")
    return found


def check_sound_constants(data, entry, fields):
    """A sound entry's fields of fixed values must hold them: QuickTime's
    compression ID and packet size (ISO's pre_defined and reserved) and,
    in version 2, its constants and the size of its fields."""
    compression, packet, fraction = struct.unpack_from(">hHxxH", data, entry.content + 20)
    if fields == SOUND_FIELDS + QUICKTIME_SOUND[2]:
        rate, _, _, bits, flags, packet_bytes, frames = struct.unpack_from(">dIIIIII", data, entry.content + 32)
        good = (data[entry.content + 16:entry.content + 32] == V2_CONSTANTS
                and data[entry.content + 44:entry.content + 48] == V2_MARK
                and math.isfinite(rate) and rate.is_integer() and 0 < rate <= MAX_RATE
                and (flags & ~LPCM_FLAGS == 0 if entry.kind == LPCM else flags <= MAX_FLAGS)
                and bits <= 64 and packet_bytes <= MAX_PACKET and frames <= MAX_PACKET)
    else:
        good = compression in (0, -1, -2) and not packet and not fraction  # a rate of whole hertz
    if not good:
        raise unsupported("values of no known meaning in a sound sample entry")


def entry_boxes(data, entry, fields):
    """The boxes of a sample entry. QuickTime may end the list with a zero
    32-bit terminator, which is no box."""
    found, position = [], entry.content + fields
    while entry.end - position >= 8:
        size = int.from_bytes(data[position:position + 4], "big")
        if size < 8 or position + size > entry.end:
            break
        found.append(bmff.Box(bytes(data[position + 4:position + 8]), position, position + 8, position + size))
        position += size
    if entry.end - position > 4 or any(data[position:entry.end]):
        raise StructureError("invalid sample entry")
    return found


def entry_fields(entry, track_handler):
    """The fields of a sample entry that are cleared: its reserved bytes, in
    a visual or sound entry its vendor code and, in a visual one, its data
    size and compressor name."""
    spans = [(entry.content, entry.content + SAMPLE_RESERVED)]
    if track_handler in VISUAL_HANDLERS or track_handler == b"soun":
        spans.append((entry.content + VENDOR[0], entry.content + VENDOR[1]))
    if track_handler in VISUAL_HANDLERS:
        spans.append((entry.content + DATA_SIZE[0], entry.content + DATA_SIZE[1]))
        spans.append((entry.content + COMPRESSOR_NAME[0], entry.content + COMPRESSOR_NAME[1]))
    return spans


def entry_box_fields(data, found, children):
    """The fields of a kept sample entry box that are cleared, at any depth
    (`children` as for check_entry_box): a codec maker's name."""
    spans = [(found.content, found.content + ENTRY_VENDORS[found.kind])] if found.kind in ENTRY_VENDORS else []
    if children is not None:
        for child in bmff.boxes(data, found.content, found.end):
            spans += entry_box_fields(data, child, children.get(child.kind))
    return spans


def check_entry_repeats(data, children, kept):
    """A sample entry holds each kept box type once, but for a colr of each
    kind: coded primaries and transfer (nclx, nclc), and an ICC profile."""
    kinds = Counter((found.kind, holds_profile(data, found)) for found in children if found.kind in kept)
    if any(count > 1 for count in kinds.values()):
        raise unsupported("a repeated box in a sample entry")


def check_entry_box(data, found, children, siblings, entry):
    """A box in the sample `entry` must have exactly its type's layout, and a
    box of boxes (children not None) only the boxes listed, at any depth, each
    once and a terminator last. `siblings` are the boxes its container may hold."""
    size = found.end - found.content
    if found.kind == b"colr":
        kind = bytes(data[found.content:found.content + 4])
        if kind not in COLOR_SIZES and kind not in PROFILE_COLORS:
            raise unsupported("colr box of type " + kind.decode("latin-1"))
        sizes = COLOR_SIZES.get(kind, (size,))
    elif found.kind == b"chan":
        sizes = (size,) if channel_layout(data, found, sound_fields(data, entry)[1]) else ()
    elif found.kind == b"auxi":  # the kind of auxiliary image, in a full box or, as Apple writes it, not: only alpha
        urn = bytes(data[found.content + (0 if any(data[found.content:found.content + 4]) else 4):found.end])
        sizes = (size,) if urn[-1:] == b"\0" and urn[:-1] in ALPHA else ()
    elif found.kind in KNOWN_PAYLOADS:
        sizes = (size,) if bytes(data[found.content:found.end]) in KNOWN_PAYLOADS[found.kind] else ()
    elif found.kind == b"must":  # the box types a reader must understand, all of them listed ones
        listed = {bytes(data[p:p + 4]) for p in range(found.content + 4, found.end, 4)}
        sizes = (size,) if size >= 4 and size % 4 == 0 and not any(data[found.content:found.content + 4]) and \
            listed <= set(siblings) else ()
    else:
        sizes = (ENTRY_SIZES.get(found.kind, size),)
    if size not in sizes or found.kind in FULL_BOXES and any(data[found.content:found.content + 4]):
        raise unsupported("the %s box in a sample entry is not in its layout" % found.kind.decode("latin-1"))
    configs.check(found.kind, data, found.content, found.end, sound_fields(data, entry)[1])
    offset, reserved = RESERVED.get(found.kind, (0, 0))
    if found.kind == b"chrm" and max(data[found.content:found.end]) > CHROMA_LOCATIONS or \
            found.kind == b"ccst" and int.from_bytes(data[found.content + 4:found.end], "big") & CODING_RESERVED or \
            reserved and data[found.content + offset] & reserved or \
            found.kind in KINDS and bytes(data[found.content + 4:found.end]) not in KINDS[found.kind] or \
            found.kind == b"frma" and data[found.content:found.end] != entry.kind:  # the format the entry is
        raise unsupported("values of no known meaning in the %s box" % found.kind.decode("latin-1"))
    if found.kind in DOLBY_VISION and (data[found.content + 4] & 0x03 or any(data[found.content + 5:found.end])):
        # after the compatibility ID and the metadata compression, reserved bits
        raise unsupported("reserved bits set in the Dolby Vision configuration")
    if found.kind == b"colr" and size == 11 and data[found.content + 10] & 0x7F:  # after nclx's full range flag
        raise unsupported("reserved bits set in the colr box")
    if children is not None:
        inner = list(bmff.boxes(data, found.content, found.end))
        if len({child.kind for child in inner}) != len(inner):
            raise unsupported("a repeated box in %s" % found.kind.decode("latin-1"))
        for index, child in enumerate(inner):
            if child.kind not in children or (child.kind == b"\0\0\0\0" and index != len(inner) - 1):
                raise unsupported("%s box in %s" % (child.kind.decode("latin-1"), found.kind.decode("latin-1")))
            check_entry_box(data, child, children[child.kind], children, entry)


def channel_layout(data, found, channels):
    """Whether a QuickTime channel layout (chan) of sound of `channels`
    describes just them: a tag of a known kind for that many channels, a
    bitmap naming them, or a description of each, 20 bytes: a label of a known
    kind, and no flags or coordinates."""
    size = found.end - found.content
    if size < 16 or not channels:
        return False
    tag, bitmap, count = struct.unpack_from(">III", data, found.content + 4)
    if tag == USE_BITMAP:
        good = not count and not bitmap & ~CHANNEL_BITS and bin(bitmap).count("1") == channels
    elif tag == USE_DESCRIPTIONS:
        good = not bitmap and count == channels <= CHANNELS
    else:  # a layout's tag, of how many channels it has
        good = not bitmap and not count and (100 <= tag >> 16 <= 255 or tag >> 16 == UNKNOWN_LAYOUT) and \
            tag & 0xFFFF == channels
    if not good or size != 16 + 20 * count:
        return False
    for position in range(found.content + 16, found.end, 20):
        label = int.from_bytes(data[position:position + 4], "big")
        named = label <= 255 or label == 0xFFFFFFFF or label in LABELS
        if not (named or label >> 16 in NUMBERED_LABELS and label & 0xFFFF < channels) or \
                any(data[position + 4:position + 20]):  # flags and coordinates
            return False
    return True


def profiles_in(data, entry, fields):
    """(start, end) of the ICC profile in each colr box of a sample entry."""
    for found in entry_boxes(data, entry, fields):
        if holds_profile(data, found):
            yield found.content + 4, found.end


def holds_profile(data, found):
    """Whether a box is a colr box holding an ICC profile."""
    return found.kind == b"colr" and bytes(data[found.content:found.content + 4]) in PROFILE_COLORS


def check_layout(data, found):
    """A box with a defined layout must have exactly its size."""
    size = found.end - found.content
    version = data[found.content] if size else 0
    expected = FIXED_SIZES.get(found.kind)
    if isinstance(expected, tuple):
        expected = expected[version] if version in (0, 1) else None
    elif found.kind in TABLES:
        before, entry = TABLES[found.kind]
        count = int.from_bytes(data[found.content + before:found.content + before + 4], "big") if size >= 8 else 0
        flags = int.from_bytes(data[found.content + 1:found.content + 4], "big") if size >= 8 else None
        expected = (before + 4 + count * entry[version] if size >= 8 and version in TABLE_VERSIONS.get(found.kind, (0,))
                    and flags in TABLE_FLAGS.get(found.kind, (0,)) else None)
    elif found.kind == b"stsz":
        fixed, count = struct.unpack_from(">II", data, found.content + 4) if size >= 12 else (0, 0)
        expected = (12 + (0 if fixed else 4 * count) if size >= 12 and not any(data[found.content:found.content + 4])
                    else None)
    elif found.kind == b"hdlr":
        component, subtype = (bytes(data[position:position + 4]) for position in (found.content + 4, found.content + 8))
        expected = size if size >= HANDLER_FIELDS and component in COMPONENTS and \
            subtype in (COMPONENTS[component] or {subtype}) else None
    elif found.kind == b"dref":
        count = int.from_bytes(data[found.content + 4:found.content + 8], "big") if size >= 8 else None
        if size < 8 or count != len(list(bmff.boxes(data, found.content + 8, found.end))):
            expected = None
        else:
            expected = size
    elif found.kind not in FIXED_SIZES:
        return
    if size != expected:
        raise unsupported("the %s box is not in its layout" % found.kind.decode("latin-1"))


def cleared_fields(data, found):
    """The spans of a box that are cleared: creation and modification times,
    fields no reader uses (see UNUSED), a handler's vendor code and name, data
    reference locations. The box has its layout (see check_layout)."""
    if found.kind in TIMED:
        version = 1 if found.end > found.content and data[found.content] == 1 else 0  # version 1: 64-bit times
        if found.end - found.content < 4 + (8 << version):
            raise StructureError("truncated movie header")
        return [(found.content + 4, found.content + 4 + (8 << version))] + [
            (found.content + start, found.content + end) for start, end in UNUSED.get(found.kind, ([], []))[version]]
    if found.kind in UNUSED:
        return [(found.content + start, found.content + end) for start, end in UNUSED[found.kind][0]]
    if found.kind == b"hdlr":
        return [(found.content + HANDLER_FIELDS, found.end)] if found.end - found.content > HANDLER_FIELDS else []
    if found.kind == b"dref":
        return data_references(data, found)
    return []


def data_references(data, dref):
    """Media must be in this file: each entry is a self-reference. Any location
    after an entry's flags is returned for clearing."""
    if dref.end - dref.content < 8:
        raise StructureError("truncated data references")
    spans = []
    for entry in bmff.boxes(data, dref.content + 8, dref.end):
        if entry.kind not in SELF_REFERENCES or data[entry.content:entry.content + 4] != b"\0\0\0\1":
            raise unsupported("external media reference")
        spans.append((entry.content + 4, entry.end))
    return spans


def empty(buffer, found):
    """Turn a box into a zero-filled free box of the same size, in place."""
    buffer[found.start + 4:found.start + 8] = b"free"
    zero(buffer, found.content, found.end)


def zero(buffer, start, end):
    for position in range(start, end, CHUNK):
        stop = min(position + CHUNK, end)
        buffer[position:stop] = bytes(stop - position)


def clear(buffer, spans):
    for start, end in spans:
        zero(buffer, start, end)


# ---------------------------------------------------------------- sample tables

def track_table(data, trak):
    """A track's sample table, read and checked: one mdia, minf and stbl; in
    it one sample description, stts, stsc, stsz and chunk offset table (stco
    or co64), and the optional tables, each in its exact layout, all agreeing
    on the number of samples; every chunk holding samples of a listed
    description, none of them empty or sharing its bytes with another, and
    every description used. Tables of samples are read a block at a time: a
    long video has millions."""
    stbl = only(data, only(data, only(data, trak, b"mdia"), b"minf"), b"stbl")
    parts = {}
    for found in bmff.boxes(data, stbl.content, stbl.end):
        if found.kind in TABLE_PARTS:
            if found.kind in parts and found.kind not in REPEATABLE or {found.kind, *parts} >= {b"stco", b"co64"}:
                raise StructureError("repeated %s box" % found.kind.decode("latin-1"))
            parts.setdefault(found.kind, []).append(found)
    if b"stz2" in parts:
        raise unsupported("compact sample sizes")
    for kind in (b"stsd", b"stts", b"stsc", b"stsz"):
        if kind not in parts:
            raise StructureError("sample table without %s" % kind.decode("latin-1"))
    if b"stco" not in parts and b"co64" not in parts:
        raise StructureError("sample table without chunk offsets")
    for found in [box for boxes in parts.values() for box in boxes]:
        check_layout(data, found)
    (stsd,), (stts,), (stsc,), (stsz,) = (parts[kind] for kind in (b"stsd", b"stts", b"stsc", b"stsz"))
    if stsd.end - stsd.content < 8:
        raise StructureError("truncated sample description")
    entries = int.from_bytes(data[stsd.content + 4:stsd.content + 8], "big")
    count = int.from_bytes(data[stsz.content + 8:stsz.content + 12], "big")
    if count and not entries:
        raise StructureError("samples without a description")
    for kind in (b"stts", b"ctts"):
        if kind in parts:
            total = 0
            for samples, _ in entries_of(data, parts[kind][0], ">II"):
                if not samples:
                    raise StructureError("an empty %s entry" % kind.decode("latin-1"))
                total += samples
            if total != count:
                raise StructureError("the %s table does not match the samples" % kind.decode("latin-1"))
    for kind in (b"stss", b"stps"):
        if kind in parts:
            previous = 0
            for number, in entries_of(data, parts[kind][0], ">I"):
                if not previous < number <= count:
                    raise StructureError("invalid %s table" % kind.decode("latin-1"))
                previous = number
    spare = check_sample_groups(data, parts, count)
    runs = list(entries_of(data, stsc, ">III"))  # from chunk `first` on, each holds `samples` of description `index`
    offsets = parts.get(b"co64") or parts[b"stco"]
    chunks = [offset for offset, in entries_of(data, offsets[0], ">Q" if offsets[0].kind == b"co64" else ">I")]
    if (runs and runs[0][0] != 1 or chunks and not runs or any(a[0] >= b[0] for a, b in zip(runs, runs[1:]))
            or any(first > len(chunks) or not samples or not 0 < index <= entries for first, samples, index in runs)
            or count and len({index for _, _, index in runs}) != entries):  # each description used
        raise StructureError("invalid sample-to-chunk table")
    unit = sound_unit(data, trak, stsd, stts, stsz)
    fixed = int.from_bytes(data[stsz.content + 4:stsz.content + 8], "big")
    sizes = sample_sizes(data, stsz)
    ranges, sample, run = [], 0, -1
    for index, offset in enumerate(chunks, 1):
        while run + 1 < len(runs) and runs[run + 1][0] <= index:
            run += 1
        per_chunk = runs[run][1] if run >= 0 else 0
        if sample + per_chunk > count:
            raise StructureError("sample table does not match its samples")
        if unit and per_chunk % unit[1]:
            raise unsupported("sound chunks of part of a packet")
        length = (per_chunk * unit[0] // unit[1] if unit else per_chunk * fixed if fixed
                  else sum(itertools.islice(sizes, per_chunk)))
        if offset + length > len(data):
            raise StructureError("samples past the end of the file")
        if not length:
            raise unsupported("a chunk of empty samples")
        ranges.append((offset, offset + length))
        sample += per_chunk
    if sample != count:
        raise StructureError("sample table does not match its samples")
    ordered = sorted(ranges)
    if any(b[0] < a[1] for a, b in zip(ordered, ordered[1:])):
        raise unsupported("chunks sharing their bytes")
    return Table(ranges, count, stsd, stsz, spare)


def only(data, container, kind):
    """The one box of a kind in a container."""
    found = [box for box in bmff.boxes(data, container.content, container.end) if box.kind == kind]
    if len(found) != 1:
        raise StructureError("track without its %s box" % kind.decode("latin-1"))
    return found[0]


def entries_of(data, table, layout, before=4):
    """The entries of a table after its version, flags and count, a block at a time."""
    size = struct.calcsize(layout)
    count = int.from_bytes(data[table.content + before:table.content + before + 4], "big")
    start = table.content + before + 4
    for first in range(0, count, BLOCK):
        stop = min(count, first + BLOCK)
        yield from struct.iter_unpack(layout, bytes(data[start + size * first:start + size * stop]))


def sample_sizes(data, stsz):
    """The size of each sample, from a checked stsz."""
    fixed = int.from_bytes(data[stsz.content + 4:stsz.content + 8], "big")
    if fixed:
        return itertools.repeat(fixed, int.from_bytes(data[stsz.content + 8:stsz.content + 12], "big"))
    return (size for size, in entries_of(data, stsz, ">I", before=8))


def check_sample_groups(data, parts, count):
    """The optional tables of samples: dependencies (sdtp), with no reserved
    value, and the sample groups that are kept (see GROUPINGS): each
    description of its type's size, each group of at least one sample, within
    the samples and descriptions. Return the spans of the descriptions no
    group uses and that are not the default, which are cleared. A description
    of a grouping without groups must be the only one, as Apple writes its
    temporal layers."""
    for sdtp in parts.get(b"sdtp", []):
        if (sdtp.end - sdtp.content != 4 + count or any(data[sdtp.content:sdtp.content + 4])
                or bytes(data[sdtp.content + 4:sdtp.end]).translate(SDTP_RESERVED).count(1)):
            raise unsupported("the sdtp box is not in its layout")
    descriptions = {grouping(data, sgpd): group_descriptions(data, sgpd)[:2] for sgpd in parts.get(b"sgpd", [])
                    if grouping(data, sgpd) in GROUPINGS}
    used = {kind: set() for kind in descriptions}
    for sbgp in parts.get(b"sbgp", []):
        kind, version = grouping(data, sbgp), data[sbgp.content]
        if kind not in descriptions:
            continue  # emptied (see clean_container)
        # Version 1 adds a grouping parameter, which no kept grouping type defines.
        if version not in (0, 1) or any(data[sbgp.content + 1:sbgp.content + 4]) or \
                version == 1 and any(data[sbgp.content + 8:sbgp.content + 12]):
            raise unsupported("the sbgp box is not in its layout")
        before = 8 if version == 1 else 4  # after the grouping type, and its parameter in version 1
        if sbgp.end - sbgp.content < 8 + before or \
                sbgp.end - sbgp.content != 8 + before + 8 * int.from_bytes(data[sbgp.content + before + 4:
                                                                               sbgp.content + before + 8], "big"):
            raise unsupported("the sbgp box is not in its layout")
        grouped = 0
        for samples, index in entries_of(data, sbgp, ">II", before=before + 4):
            grouped += samples
            if not samples or index > descriptions[kind][0]:
                raise unsupported("invalid sample group")
            used[kind].add(index)
        if grouped > count:
            raise unsupported("invalid sample group")
    spare = []
    for sgpd in parts.get(b"sgpd", []):
        kind = grouping(data, sgpd)
        if kind in descriptions and any(grouping(data, sbgp) == kind for sbgp in parts.get(b"sbgp", [])):
            spare += group_descriptions(data, sgpd, used[kind] | {descriptions[kind][1]})[2]
        elif kind in descriptions and descriptions[kind][0] > 1:
            raise unsupported("sample group descriptions no sample uses")
    return spare


def group_descriptions(data, sgpd, used=None):
    """(the number of descriptions, the default one's index or 0, spans) of a
    sample group description of a kept type: with `used`, the indices of the
    descriptions in use, the spans are those of the others, merged where
    they meet."""
    kind, version = grouping(data, sgpd), data[sgpd.content]
    size, reserved = GROUPINGS[kind]
    position = sgpd.content + 8
    if version not in (0, 1, 2) or any(data[sgpd.content + 1:sgpd.content + 4]):
        raise unsupported("the sgpd box is not in its layout")
    length, default = size, 0
    if version:
        length = int.from_bytes(data[position:position + 4], "big")
        position += 4
    if version == 2:
        default = int.from_bytes(data[position:position + 4], "big")
        position += 4
    count = int.from_bytes(data[position:position + 4], "big")
    position += 4
    if default > count:
        raise unsupported("the sgpd box is not in its layout")
    spans = []
    for index in range(1, count + 1):
        if length == 0:  # each entry says its own length
            if int.from_bytes(data[position:position + 4], "big") != size:
                raise unsupported("the sgpd box is not in its layout")
            position += 4
        elif length != size:
            raise unsupported("the sgpd box is not in its layout")
        if position + size > sgpd.end or data[position] & reserved:
            raise unsupported("the sgpd box is not in its layout")
        if used is not None and index not in used:
            if spans and spans[-1][1] == position:
                spans[-1] = spans[-1][0], position + size
            else:
                spans.append((position, position + size))
        position += size
    if position != sgpd.end:
        raise unsupported("the sgpd box is not in its layout")
    return count, default, spans


def sound_unit(data, trak, stsd, stts, stsz):
    """(bytes, frames) that a sound track's chunks hold, or None where players
    read them sample by sample, by stsz. FFmpeg reads sound whose table
    counts frames of one time unit each (one stts entry of delta 1) chunk by
    chunk: packets of several frames by the entry's packet size, else frames
    by their channels and bits, or by stsz's size if it is compressed.
    AVFoundation reads it by the entry. So all of these must agree, and chunks
    hold whole packets (see track_table); sound the players could read in two
    ways is refused, and so are several or version 1 sample descriptions,
    which FFmpeg reads by the file's brands."""
    if handler(data, trak) != b"soun":
        return None
    fixed = int.from_bytes(data[stsz.content + 4:stsz.content + 8], "big")
    durations = list(itertools.islice(entries_of(data, stts, ">II"), 2))
    chunked = len(durations) == 1 and durations[0][1] == 1
    entries = list(bmff.boxes(data, stsd.content + 8, stsd.end))
    pcm = any(entry.kind in PCM for entry in entries)
    if not (chunked or pcm):
        return None
    if data[stsd.content] or len(entries) != 1:
        raise unsupported("sound whose samples could be read in two ways")
    frame, packet = frame_size(data, entries[0]), packet_size(data, entries[0])
    if pcm and not frame:
        raise unsupported("sound whose sample sizes are not given")
    if entries[0].kind in PACKED:
        per_channel, frames = PACKED[entries[0].kind]
        unit = per_channel * sound_fields(data, entries[0])[1], frames
        if packet and packet != unit or not unit[0]:
            raise unsupported("sound whose samples could be read in two ways")
        packet = unit
    if packet and packet[1] > 1:  # AVFoundation reads uncompressed sound by its frames
        if not (chunked and packet[0]) or frame and packet[0] != packet[1] * frame:
            raise unsupported("sound whose samples could be read in two ways")
        return packet
    size = frame or fixed
    if packet and packet[0] != size or entries[0].kind in ISO_PCM and fixed != frame:  # FFmpeg reads ISO's by stsz
        raise unsupported("sound whose samples could be read in two ways")
    if chunked:
        if fixed in (0, 1) and not frame or frame and fixed not in (1, frame):
            raise unsupported("sound whose sample sizes are not given")
        return size, 1
    if fixed != frame:  # uncompressed, read sample by sample: by stsz, but no less than a frame
        raise unsupported("sound whose samples could be read in two ways")
    return None


def frame_size(data, entry):
    """The bytes of a frame of uncompressed sound, by its channels and bits
    per sample as FFmpeg reads them; None for any other entry, and for bits
    that FFmpeg and AVFoundation read differently."""
    version, channels, bits = sound_fields(data, entry)
    if version is None:
        return None
    if entry.kind in ISO_PCM and version == 0:
        bits = next((data[found.content + 5] for found in entry_boxes(data, entry, SOUND_FIELDS)
                     if found.kind == b"pcmC" and found.end - found.content == ENTRY_SIZES[b"pcmC"]), None)
    elif entry.kind in PCM_BITS:  # of its type's size, which a version 2 entry must give too
        bits = PCM_BITS[entry.kind] if version < 2 or bits == PCM_BITS[entry.kind] else None
    elif entry.kind in ENTRY_BITS:
        bits = bits if bits in ENTRY_BITS[entry.kind] else None
    elif entry.kind != LPCM or version != 2:
        return None
    elif flags(data, entry) & LPCM_FLOAT and bits not in (32, 64) or bits == 64 and not flags(data, entry) & (
            LPCM_FLOAT | LPCM_SIGNED):
        return None  # sizes of float and unsigned numbers FFmpeg reads as no sound at all
    return channels * bits // 8 if bits in SAMPLE_BITS else None


def flags(data, entry):
    """The format flags of a version 2 sound entry."""
    return int.from_bytes(data[entry.content + 52:entry.content + 56], "big")


def sound_fields(data, entry):
    """(version, channels, bits per sample) of a sound sample entry; the
    version is None for one of no known layout."""
    fields = entry.content
    version = int.from_bytes(data[fields + 8:fields + 10], "big")
    if version == 2 and entry.end - fields >= 64:  # channels, then constant bits per channel
        return (version, *(int.from_bytes(data[fields + start:fields + start + 4], "big") for start in (40, 48)))
    if version in (0, 1):
        return (version, *(int.from_bytes(data[fields + start:fields + start + 2], "big") for start in (16, 18)))
    return None, 0, 0


def packet_size(data, entry):
    """(bytes, frames) of a packet of QuickTime sound of version 1 or 2; None for other entries."""
    fields = entry.content
    version = int.from_bytes(data[fields + 8:fields + 10], "big")
    if version == 1 and entry.end - fields >= 44:  # samples per packet, then bytes per frame
        samples, _, frame = struct.unpack_from(">III", data, fields + 28)
        return frame, samples
    if version == 2 and entry.end - fields >= 64:  # bytes and frames per packet
        return struct.unpack_from(">II", data, fields + 56)
    return None


def movie_ranges(data, moov):
    """(start, end) of the media the tracks of a movie use: their chunks,
    sorted, and merged where they meet or overlap, so that checking them costs
    no more than the media."""
    return merged(span for trak in bmff.boxes(data, moov.content, moov.end) if trak.kind == b"trak"
                  for span in track_table(data, trak).ranges)


def merged(spans):
    """(start, end) spans sorted, and merged where they meet or overlap; empty
    ones, which hold no byte, are left out."""
    result = []
    for start, end in sorted(spans):
        if start == end:
            continue
        if result and start <= result[-1][1]:
            result[-1] = result[-1][0], max(result[-1][1], end)
        else:
            result.append((start, end))
    return result


def overlaps(spans, start, end):
    """Whether [start, end) shares a byte with the merged, sorted spans."""
    index = bisect.bisect_left(spans, (end,)) - 1  # the last span starting before end
    return start < end and index >= 0 and spans[index][1] > start


# ------------------------------------------------------------------------ media

def gaps(used, start, end):
    """Parts of [start, end) that no used range covers; `used` is sorted."""
    return uncovered(used, [(start, end)])


def uncovered(used, spans):
    """Parts of the sorted, disjoint spans that no used range covers, in one
    sweep; `used` is sorted."""
    first = 0
    for start, end in spans:
        while first < len(used) and used[first][1] <= start:
            first += 1
        position, index = start, first
        while index < len(used) and used[index][0] < end:
            if used[index][0] > position:
                yield position, used[index][0]
            position = max(position, used[index][1])
            index += 1
        if position < end:
            yield position, end


def inside(used, spans):
    """Whether every non-empty used range lies in one of the sorted, disjoint spans."""
    starts = [start for start, _ in spans]
    for a, b in used:
        index = bisect.bisect_right(starts, a) - 1
        if a < b and (index < 0 or b > spans[index][1]):
            return False
    return True


def zeroed(data, spans):
    """Whether every byte in the given (start, end) spans is zero."""
    for start, end in spans:
        for position in range(start, end, CHUNK):
            stop = min(position + CHUNK, end)
            if data[position:stop].count(0) != stop - position:
                return False
    return True


def same(original, rebuilt, spans):
    """Whether the result holds the original's bytes in the given spans."""
    for start, end in spans:
        for position in range(start, end, CHUNK):
            stop = min(position + CHUNK, end)
            if original[position:stop] != rebuilt[position:stop]:
                return False
    return True


# ------------------------------------------------------------------ verification

def check(original, rebuilt, moov, policy):
    """A cleaned movie box, checked on its own and against the original's, which
    lies at the same place: each box is one the policy keeps, holding the
    original's bytes but for the cleared fields, or a zero-filled free box.
    [Track] of the tracks kept."""
    if rebuilt[moov.start:moov.content] != original[moov.start:moov.content]:
        fail("movie box moved")
    found = tracks(rebuilt, moov)
    idents = {track.ident for track in found}
    for track in found:
        if track.handler not in policy.entries or (track.handler in policy.removed and not (
                policy.rendering and policy.rendering(rebuilt, track))):
            fail("%r track kept" % track.handler)
        for kind, targets in track.references:
            if not set(targets) <= idents or (policy.references is not None and kind not in policy.references):
                fail("reference to a removed track")
    spare = [span for track in found for span in track_table(rebuilt, track.box).spare]
    check_container(original, rebuilt, moov, policy, None, spare)
    return found


def check_container(original, rebuilt, container, policy, track_handler, spare):
    children = list(bmff.boxes(rebuilt, container.content, container.end))
    allowed = policy.boxes[container.kind]
    check_repeats(rebuilt, children, allowed)
    groups = kept_groupings(rebuilt, children) if container.kind == b"stbl" else set()
    for found in children:
        if found.kind == b"free" and zeroed(rebuilt, [(found.content, found.end)]):
            continue
        if found.kind not in allowed or found.kind in (b"sgpd", b"sbgp") and grouping(rebuilt, found) not in groups:
            fail("box %r kept" % found.kind)
        if rebuilt[found.start:found.content] != original[found.start:found.content]:
            fail("box %r changed" % found.kind)
        if found.kind in policy.boxes:
            check_container(original, rebuilt, found, policy,
                            handler(rebuilt, found) if found.kind == b"trak" else track_handler, spare)
        elif found.kind == b"tref":
            for reference in bmff.boxes(rebuilt, found.content, found.end):
                if reference.kind == b"free":
                    if not zeroed(rebuilt, [(reference.content, reference.end)]):
                        fail("data in a track reference")
                elif not matches(original, rebuilt, reference, []):
                    fail("track reference changed")
        elif found.kind == b"stsd":
            check_entries(original, rebuilt, found, policy, track_handler)
        else:
            check_layout(rebuilt, found)
            cleared = cleared_fields(rebuilt, found) + [span for span in spare if found.content <= span[0] < found.end]
            if not matches(original, rebuilt, found, cleared):
                fail("box %r changed, or names or times kept" % found.kind)


def check_entries(original, rebuilt, stsd, policy, track_handler):
    kept = policy.entries[track_handler][1]
    if not matches(original, rebuilt, bmff.Box(stsd.kind, stsd.start, stsd.content, stsd.content + 8), []):
        fail("sample description changed")
    for entry, fields in sample_entries(rebuilt, stsd, track_handler, policy):
        header = bmff.Box(entry.kind, entry.start, entry.content, entry.content + fields)
        if not matches(original, rebuilt, header, entry_fields(entry, track_handler)):
            fail("sample entry changed, or its names kept")
        children = entry_boxes(rebuilt, entry, fields)
        tail = bmff.Box(b"", children[-1].end, children[-1].end, entry.end) if children else None
        if tail and not matches(original, rebuilt, tail, []):  # a terminator
            fail("sample entry changed")
        check_entry_repeats(rebuilt, children, kept)
        sanitized = dict(profiles_in(original, entry, fields))
        for child in children:
            if child.kind == b"free" and zeroed(rebuilt, [(child.content, child.end)]) and (
                    not policy.strict or bytes(original[child.start + 4:child.content]) in {b"free"} | set(
                        (policy.emptied or {}).get(entry.kind, ()))):
                continue
            if child.kind not in kept:
                fail("sample entry box %r kept" % child.kind)
            check_entry_box(rebuilt, child, kept[child.kind], kept, entry)
            profile = sanitized.get(child.content + 4)
            if profile:
                expected = bytes(original[child.start:child.content + 4]) + icc.sanitize(
                    bytes(original[child.content + 4:profile]))
                if rebuilt[child.start:child.end] != expected:
                    fail("color profile not sanitized")
            elif not matches(original, rebuilt, child, entry_box_fields(rebuilt, child, kept[child.kind])):
                fail("sample entry box %r changed" % child.kind)


def matches(original, rebuilt, found, spans):
    """Whether a box holds the original's bytes, but zeros in the given spans."""
    expected = bytearray(original[found.start:found.end])
    for start, end in spans:
        expected[start - found.start:end - found.start] = bytes(end - start)
    return rebuilt[found.start:found.end] == expected


def fail(detail):
    raise VerificationError("verification_failed", detail=detail)
