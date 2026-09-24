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
import struct
from collections import Counter
from dataclasses import dataclass
from typing import NamedTuple

from .. import icc
from ..errors import VerificationError
from . import bmff
from .bmff import StructureError, unsupported

TIMED = {b"mvhd", b"tkhd", b"mdhd"}
HANDLER_FIELDS = 12        # a handler's version, flags and types; its vendor, flags and name follow
VISUAL_FIELDS = 78         # bytes of a visual sample entry before its boxes
SOUND_FIELDS = 28          # of a sound sample entry; QuickTime's versions 1 and 2 add more
SAMPLE_FIELDS = 8          # of any other: reserved bytes and a data reference index
QUICKTIME_SOUND = {1: 16, 2: 36}
VISUAL_HANDLERS = {b"pict", b"vide", b"auxv"}
VENDOR = (12, 16)          # within a visual or sound entry: QuickTime's vendor code
COMPRESSOR_NAME = (42, 74)  # within a visual entry
# Payload sizes of fixed-size boxes, by version where they have versions 0 and 1.
FIXED_SIZES = {b"mvhd": (100, 112), b"tkhd": (84, 96), b"mdhd": (24, 36), b"cslg": (24, 44), b"mehd": (8, 12),
               b"vmhd": 12, b"smhd": 8, b"nmhd": 4, b"gmin": 16, b"trex": 24, b"clef": 12, b"prof": 12, b"enof": 12}
# Tables of a count and that many entries: (bytes before the count, entry size by version).
TABLES = {b"stts": (4, (8, 8)), b"ctts": (4, (8, 8)), b"stss": (4, (4, 4)), b"stps": (4, (4, 4)),
          b"stsc": (4, (12, 12)), b"stco": (4, (4, 4)), b"co64": (4, (8, 8)), b"elst": (4, (12, 20)),
          b"stsh": (4, (8, 8))}
# Boxes that belong in one place of a movie; one found anywhere else means a damaged file.
PLACED = {b"trak", b"tkhd", b"edts", b"elst", b"mdia", b"mdhd", b"minf", b"stbl", b"stsd", b"stts", b"ctts",
          b"stsc", b"stsz", b"stco", b"co64", b"stss"}
REPEATABLE = {b"trak", b"sgpd", b"sbgp"}  # the others appear once in their container
# The boxes of a sample table that track_table reads.
TABLE_PARTS = {b"stsd", b"stts", b"ctts", b"stsc", b"stsz", b"stz2", b"stco", b"co64", b"stss", b"stps", b"sdtp",
               b"padb", b"subs", b"sgpd", b"sbgp"}
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
               b"mp4a": 4, b"\0\0\0\0": 0, b"dac3": 3, b"damr": 9, b"d263": 7, b"pcmC": 6, b"srat": 8,
               b"SmDm": 28, b"CoLL": 8,  # VP9's mastering display and light levels
               # spatial video: stereo views, hero eye, baseline, disparity, projection, packing
               b"stri": 5, b"hero": 5, b"blin": 8, b"dadj": 8, b"prji": 8, b"pkin": 8}
COLOR_SIZES = {b"nclx": (11, 10), b"nclc": (10,)}  # some Android phones leave out nclx's range byte
PROFILE_COLORS = {b"prof", b"rICC"}  # ICC profiles, sanitized
DOLBY_VISION = {b"dvcC", b"dvvC", b"dvwC"}
# Leading bytes of 3GPP decoder boxes that name the codec's maker, cleared.
ENTRY_VENDORS = {b"d263": 4, b"damr": 4}
# Data references that mean "in this file" when their flag 1 is set.
SELF_REFERENCES = {b"url ", b"urn ", b"alis"}
# Uncompressed QuickTime sound: bits per sample, or None to take the entry's own.
PCM_BITS = {b"twos": None, b"sowt": None, b"raw ": None, b"in24": 24, b"in32": 32, b"fl32": 32, b"fl64": 64,
            b"ulaw": 8, b"alaw": 8}
LANGUAGE = 64  # longest extended language tag kept (elng)
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
    references: track reference types kept between kept tracks; None keeps
        all of them as they are.
    dangling: reference types from a kept track that go with the removed
        tracks they point to.
    strict: refuse sample entries holding boxes the policy does not list,
        rather than emptying those boxes.
    rendering: a function (data, track) telling whether a track of a removed
        handler type is one that another track is shown with ('rndr'), and
        that the policy recognizes exactly; such a track is kept. A removed
        track that another is shown with is refused otherwise.
    """
    boxes: dict
    entries: dict
    removed: frozenset = frozenset()
    chapters: bool = False
    references: frozenset = None
    dangling: frozenset = frozenset()
    strict: bool = False
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


def removed_tracks(data, found, policy):
    """The IDs of the tracks that go. Any other track the policy does not keep
    is refused, and so is a reference that removing them would break."""
    chapters = {ident for track in found for kind, idents in track.references if kind == b"chap" for ident in idents}
    removed = {track.ident for track in found
               if (track.handler in policy.removed or (policy.chapters and track.handler == b"text"
                                                       and track.ident in chapters))
               and not (policy.rendering and policy.rendering(data, track))}
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

def clean(buffer, moov, policy):
    """Clean a movie box in place, as the policy says; [Track] of those kept."""
    found = tracks(buffer, moov)
    removed = removed_tracks(buffer, found, policy)
    for track in found:
        if track.ident in removed:
            empty(buffer, track.box)
        else:
            track_table(buffer, track.box)
    clean_container(buffer, moov, policy, None, removed)
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
        elif found.kind == b"tref" and policy.references is not None:
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
                check_entry_box(buffer, child, kept[child.kind], kept)
                clear(buffer, entry_box_fields(child))
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
        found.append((entry, fields))
    if len(found) != int.from_bytes(data[stsd.content + 4:stsd.content + 8], "big"):
        raise StructureError("invalid sample entry count")
    return found


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
    """The fields of a sample entry that are cleared: in a visual or sound
    entry its vendor code and, in a visual one, its compressor name."""
    spans = []
    if track_handler in VISUAL_HANDLERS or track_handler == b"soun":
        spans.append((entry.content + VENDOR[0], entry.content + VENDOR[1]))
    if track_handler in VISUAL_HANDLERS:
        spans.append((entry.content + COMPRESSOR_NAME[0], entry.content + COMPRESSOR_NAME[1]))
    return spans


def entry_box_fields(found):
    """The fields of a sample entry box that are cleared: a codec maker's name."""
    return [(found.content, found.content + ENTRY_VENDORS[found.kind])] if found.kind in ENTRY_VENDORS else []


def check_entry_repeats(data, children, kept):
    """A sample entry holds each kept box type once, but for a colr of each
    kind: coded primaries and transfer (nclx, nclc), and an ICC profile."""
    kinds = Counter((found.kind, holds_profile(data, found)) for found in children if found.kind in kept)
    if any(count > 1 for count in kinds.values()):
        raise unsupported("a repeated box in a sample entry")


def check_entry_box(data, found, children, siblings):
    """A box in a sample entry must have exactly its type's layout, and a box
    of boxes (children not None) only the boxes listed, at any depth, each once
    and a terminator last. `siblings` are the boxes its container may hold."""
    size = found.end - found.content
    if found.kind == b"colr":
        kind = bytes(data[found.content:found.content + 4])
        if kind not in COLOR_SIZES and kind not in PROFILE_COLORS:
            raise unsupported("colr box of type " + kind.decode("latin-1"))
        sizes = COLOR_SIZES.get(kind, (size,))
    elif found.kind == b"chan":  # QuickTime channel layout: a tag, a bitmap, then 20 bytes a channel
        sizes = (16 + 20 * int.from_bytes(data[found.content + 12:found.content + 16], "big"),) if size >= 16 else ()
    elif found.kind == b"must":  # the box types a reader must understand, all of them listed ones
        listed = {bytes(data[p:p + 4]) for p in range(found.content + 4, found.end, 4)}
        sizes = (size,) if size >= 4 and size % 4 == 0 and not any(data[found.content:found.content + 4]) and \
            listed <= set(siblings) else ()
    else:
        sizes = (ENTRY_SIZES.get(found.kind, size),)
    if size not in sizes:
        raise unsupported("the %s box in a sample entry is not in its layout" % found.kind.decode("latin-1"))
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
            check_entry_box(data, child, children[child.kind], children)


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
        expected = before + 4 + count * entry[version] if version in (0, 1) and size >= 8 else None
    elif found.kind == b"stsz":
        fixed, count = struct.unpack_from(">II", data, found.content + 4) if size >= 12 else (0, 0)
        expected = 12 + (0 if fixed else 4 * count) if size >= 12 else None
    elif found.kind == b"dref":
        count = int.from_bytes(data[found.content + 4:found.content + 8], "big") if size >= 8 else None
        if size < 8 or count != len(list(bmff.boxes(data, found.content + 8, found.end))):
            expected = None
        else:
            expected = size
    elif found.kind == b"elng":  # an extended language tag, such as zh-Hant
        tag = bytes(data[found.content + 4:found.end - 1])
        expected = size if (4 < size <= 4 + LANGUAGE and not any(data[found.content:found.content + 4])
                            and data[found.end - 1] == 0 and tag and all(c in b"-" or chr(c).isalnum() and c < 128
                                                                          for c in tag)) else None
    elif found.kind not in FIXED_SIZES:
        return
    if size != expected:
        raise unsupported("the %s box is not in its layout" % found.kind.decode("latin-1"))


def cleared_fields(data, found):
    """The spans of a box that are cleared: creation and modification times,
    a handler's vendor code and name, data reference locations."""
    if found.kind in TIMED:
        size = 16 if found.end > found.content and data[found.content] == 1 else 8  # version 1: 64-bit times
        if found.end - found.content < 4 + size:
            raise StructureError("truncated movie header")
        return [(found.content + 4, found.content + 4 + size)]
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
        if entry.kind not in SELF_REFERENCES or entry.end - entry.content < 4 or not data[entry.content + 3] & 1:
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
    description, and every description used. Tables of samples are read a
    block at a time: a long video has millions."""
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
    entries = int.from_bytes(data[stsd.content + 4:stsd.content + 8], "big") if stsd.end - stsd.content >= 8 else 0
    count = int.from_bytes(data[stsz.content + 8:stsz.content + 12], "big")
    for kind in (b"stts", b"ctts"):
        if kind in parts and sum(samples for samples, _ in entries_of(data, parts[kind][0], ">II")) != count:
            raise StructureError("the %s table does not match the samples" % kind.decode("latin-1"))
    for kind in (b"stss", b"stps"):
        if kind in parts:
            previous = 0
            for number, in entries_of(data, parts[kind][0], ">I"):
                if not previous < number <= count:
                    raise StructureError("invalid %s table" % kind.decode("latin-1"))
                previous = number
    check_sample_groups(data, parts, count)
    runs = list(entries_of(data, stsc, ">III"))  # from chunk `first` on, each holds `samples` of description `index`
    offsets = parts.get(b"co64") or parts[b"stco"]
    chunks = [offset for offset, in entries_of(data, offsets[0], ">Q" if offsets[0].kind == b"co64" else ">I")]
    if (runs and runs[0][0] != 1 or any(a[0] >= b[0] for a, b in zip(runs, runs[1:]))
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
        length = (per_chunk * unit[0] // unit[1] if unit else per_chunk * fixed if fixed
                  else sum(itertools.islice(sizes, per_chunk)))
        if offset + length > len(data):
            raise StructureError("samples past the end of the file")
        ranges.append((offset, offset + length))
        sample += per_chunk
    if sample != count:
        raise StructureError("sample table does not match its samples")
    return Table(ranges, count, stsd, stsz)


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
    """The optional tables of samples: dependencies (sdtp), padding bits
    (padb), sub-samples (subs), and the sample groups that are kept (see
    GROUPINGS), each description of its type's size and each group within
    the samples and descriptions."""
    for sdtp in parts.get(b"sdtp", []):
        if sdtp.end - sdtp.content != 4 + count or any(data[sdtp.content:sdtp.content + 4]):
            raise unsupported("the sdtp box is not in its layout")
    for padb in parts.get(b"padb", []):
        padded = int.from_bytes(data[padb.content + 4:padb.content + 8], "big") if padb.end - padb.content >= 8 else -1
        if (padded != count or padb.end - padb.content != 8 + (count + 1) // 2
                or any(byte & 0x88 for byte in data[padb.content + 8:padb.end])):
            raise unsupported("the padb box is not in its layout")
    for subs in parts.get(b"subs", []):
        check_subsamples(data, subs, count)
    descriptions = {grouping(data, sgpd): group_descriptions(data, sgpd) for sgpd in parts.get(b"sgpd", [])
                    if grouping(data, sgpd) in GROUPINGS}
    for sbgp in parts.get(b"sbgp", []):
        kind, version = grouping(data, sbgp), data[sbgp.content]
        if kind not in descriptions:
            continue  # emptied (see clean_container)
        if version not in (0, 1) or any(data[sbgp.content + 1:sbgp.content + 4]):
            raise unsupported("the sbgp box is not in its layout")
        before = 8 if version == 1 else 4  # after the grouping type, and its parameter in version 1
        if sbgp.end - sbgp.content < 8 + before or \
                sbgp.end - sbgp.content != 8 + before + 8 * int.from_bytes(data[sbgp.content + before + 4:
                                                                               sbgp.content + before + 8], "big"):
            raise unsupported("the sbgp box is not in its layout")
        grouped = 0
        for samples, index in entries_of(data, sbgp, ">II", before=before + 4):
            grouped += samples
            if index > descriptions[kind]:
                raise unsupported("invalid sample group")
        if grouped > count:
            raise unsupported("invalid sample group")


def group_descriptions(data, sgpd):
    """The number of descriptions in a sample group description of a kept type."""
    kind, version = grouping(data, sgpd), data[sgpd.content]
    size, reserved = GROUPINGS[kind]
    position = sgpd.content + 8
    if version not in (0, 1, 2) or any(data[sgpd.content + 1:sgpd.content + 4]):
        raise unsupported("the sgpd box is not in its layout")
    default = size
    if version:
        default = int.from_bytes(data[position:position + 4], "big")
        position += 4 + (4 if version == 2 else 0)  # version 2: the default description's index
    count = int.from_bytes(data[position:position + 4], "big")
    position += 4
    for _ in range(count):
        if default == 0:  # each entry says its own length
            if int.from_bytes(data[position:position + 4], "big") != size:
                raise unsupported("the sgpd box is not in its layout")
            position += 4
        elif default != size:
            raise unsupported("the sgpd box is not in its layout")
        if position + size > sgpd.end or data[position] & reserved:
            raise unsupported("the sgpd box is not in its layout")
        position += size
    if position != sgpd.end:
        raise unsupported("the sgpd box is not in its layout")
    return count


def check_subsamples(data, subs, count):
    """Sub-sample information: for some samples, each after the previous one,
    their sub-samples, each a size (2 bytes, or 4 in version 1), a priority, a
    discardable flag and 4 bytes for the codec."""
    version = data[subs.content] if subs.end - subs.content >= 8 else -1
    if version not in (0, 1):
        raise unsupported("the subs box is not in its layout")
    item = (4 if version else 2) + 6
    position, samples = subs.content + 8, 0
    for _ in range(int.from_bytes(data[subs.content + 4:subs.content + 8], "big")):
        if position + 6 > subs.end:
            raise unsupported("the subs box is not in its layout")
        samples += int.from_bytes(data[position:position + 4], "big")
        position += 6 + item * int.from_bytes(data[position + 4:position + 6], "big")
    if position != subs.end or samples > count:
        raise unsupported("the subs box is not in its layout")


def sound_unit(data, trak, stsd, stts, stsz):
    """(bytes, frames) of uncompressed QuickTime sound, whose sample table
    counts audio frames, each of one time unit, and gives their size, often as
    1, whatever it is: players read whole chunks by the sizes in the sample
    entry, as ffmpeg does. None for any other track."""
    fixed = int.from_bytes(data[stsz.content + 4:stsz.content + 8], "big")
    durations = list(itertools.islice(entries_of(data, stts, ">II"), 2))
    if handler(data, trak) != b"soun" or not fixed or len(durations) != 1 or durations[0][1] != 1:
        return None
    entries = list(bmff.boxes(data, stsd.content + 8, stsd.end))
    fields = entries[0].content if len(entries) == 1 and not data[stsd.content] else None
    version = int.from_bytes(data[fields + 8:fields + 10], "big") if fields is not None else None
    unit = None
    if version == 1 and entries[0].end - fields >= 44:    # bytes per frame, samples per packet
        samples, _, frame = struct.unpack_from(">III", data, fields + 28)
        unit = frame, samples
    elif version == 2 and entries[0].end - fields >= 64:  # bytes and frames per packet
        unit = struct.unpack_from(">II", data, fields + 56)
    elif version == 0 and entries[0].kind in PCM_BITS:
        bits = PCM_BITS[entries[0].kind] or int.from_bytes(data[fields + 18:fields + 20], "big")
        unit = int.from_bytes(data[fields + 16:fields + 18], "big") * bits // 8, 1
    if unit and all(unit):
        return unit
    if unit or fixed == 1:
        raise unsupported("sound whose sample sizes are not given")
    return None


def movie_ranges(data, moov):
    """(start, end) of the chunks of samples of every track in a movie, sorted."""
    return sorted(span for trak in bmff.boxes(data, moov.content, moov.end) if trak.kind == b"trak"
                  for span in track_table(data, trak).ranges)


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
    check_container(original, rebuilt, moov, policy, None)
    for track in found:
        track_table(rebuilt, track.box)
    return found


def check_container(original, rebuilt, container, policy, track_handler):
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
                            handler(rebuilt, found) if found.kind == b"trak" else track_handler)
        elif found.kind == b"tref" and policy.references is not None:
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
            if not matches(original, rebuilt, found, cleared_fields(rebuilt, found)):
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
            if child.kind == b"free" and zeroed(rebuilt, [(child.content, child.end)]) and not policy.strict:
                continue
            if child.kind not in kept:
                fail("sample entry box %r kept" % child.kind)
            check_entry_box(rebuilt, child, kept[child.kind], kept)
            profile = sanitized.get(child.content + 4)
            if profile:
                expected = bytes(original[child.start:child.content + 4]) + icc.sanitize(
                    bytes(original[child.content + 4:profile]))
                if rebuilt[child.start:child.end] != expected:
                    fail("color profile not sanitized")
            elif not matches(original, rebuilt, child, entry_box_fields(child)):
                fail("sample entry box %r changed" % child.kind)


def matches(original, rebuilt, found, spans):
    """Whether a box holds the original's bytes, but zeros in the given spans."""
    expected = bytearray(original[found.start:found.end])
    for start, end in spans:
        expected[start - found.start:end - found.start] = bytes(end - start)
    return rebuilt[found.start:found.end] == expected


def fail(detail):
    raise VerificationError("verification_failed", detail=detail)
