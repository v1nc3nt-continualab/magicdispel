"""The movie box (moov) of ISO base media and QuickTime files, cleaned in place.

A movie keeps only the boxes that play it, from a fixed list per container
(Policy.boxes): headers, tracks and their edits, and the sample tables.
Readers skip boxes they do not know, so any other box becomes a zero-filled
free box of the same size, and nothing moves. A track is kept, removed or
refused by its handler type. A removed track is emptied, and its samples,
which no remaining table points to, are zeroed with the rest of the unused
media (see gaps). A kept track keeps sample entries of known types, and in
each only the boxes that say how to decode and show its samples. Creation
and modification times, handler and compressor names, vendor codes and data
reference locations are cleared. Boxes with a defined layout must have
exactly its size, so they cannot carry extra bytes.
"""
import struct
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
FIXED_SIZES = {b"mvhd": (100, 112), b"tkhd": (84, 96), b"mdhd": (24, 36), b"cslg": (24, 44),
               b"vmhd": 12, b"smhd": 8, b"nmhd": 4, b"gmin": 16, b"clef": 12, b"prof": 12, b"enof": 12}
# Tables of a count and that many entries: (bytes before the count, entry size by version).
TABLES = {b"stts": (4, (8, 8)), b"ctts": (4, (8, 8)), b"stss": (4, (4, 4)), b"stps": (4, (4, 4)),
          b"stsc": (4, (12, 12)), b"stco": (4, (4, 4)), b"co64": (4, (8, 8)), b"elst": (4, (12, 20))}
# Fixed-size boxes in sample entries. colr is checked by its type: nclx, QuickTime's
# nclc, or an ICC profile.
ENTRY_SIZES = {b"pasp": 8, b"clap": 32, b"fiel": 2, b"clli": 4, b"mdcv": 24, b"amve": 8, b"btrt": 12,
               b"gama": 4, b"dvcC": 24, b"dvvC": 24, b"dvwC": 24, b"hfov": 4, b"frma": 4, b"enda": 2,
               b"dac3": 3, b"damr": 9, b"pcmC": 6, b"srat": 8,
               # spatial video: stereo views, hero eye, baseline, disparity, projection, packing
               b"stri": 5, b"hero": 5, b"blin": 8, b"dadj": 8, b"prji": 8, b"pkin": 8}
COLOR_SIZES = {b"nclx": (11, 10), b"nclc": (10,)}  # some Android phones leave out nclx's range byte
# Data references that mean "in this file" when their flag 1 is set.
SELF_REFERENCES = {b"url ", b"urn ", b"alis"}
CHUNK = 1 << 20


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
    """[(type, [track IDs])] of a track's references; emptied ones are no references."""
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
    clean_container(buffer, moov, policy, None, removed)
    return [track for track in found if track.ident not in removed]


def clean_container(buffer, container, policy, track_handler, removed):
    for found in bmff.boxes(buffer, container.content, container.end):
        if found.kind not in policy.boxes[container.kind]:
            empty(buffer, found)
        elif found.kind in policy.boxes:
            clean_container(buffer, found, policy, handler(buffer, found) if found.kind == b"trak" else track_handler,
                            removed)
        elif found.kind == b"tref" and policy.references is not None:
            for reference in bmff.boxes(buffer, found.content, found.end):
                targets = {int.from_bytes(buffer[p:p + 4], "big") for p in range(reference.content, reference.end, 4)}
                if targets <= removed:  # see removed_tracks: only dangling types get here
                    empty(buffer, reference)
        elif found.kind == b"stsd":
            clean_entries(buffer, found, track_handler, policy)
        else:
            check_layout(buffer, found)
            clear(buffer, cleared_fields(buffer, found))


def clean_entries(buffer, stsd, track_handler, policy):
    """Keep in each sample entry only the boxes the policy lists for its track."""
    kept = policy.entries[track_handler][1]
    for entry, fields in sample_entries(buffer, stsd, track_handler, policy):
        clear(buffer, entry_fields(entry, track_handler))
        for child in entry_boxes(buffer, entry, fields):
            if child.kind in kept:
                check_entry_box(buffer, child, kept[child.kind])
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
    if any(data[position:entry.end]):
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


def check_entry_box(data, found, children):
    """A fixed-size box in a sample entry must have exactly its size, and a box
    of boxes (children not None) only the boxes listed, at any depth."""
    size = found.end - found.content
    if found.kind == b"colr":
        sizes = COLOR_SIZES.get(bytes(data[found.content:found.content + 4]), (size,))
    else:
        sizes = (ENTRY_SIZES.get(found.kind, size),)
    if size not in sizes:
        raise unsupported("extra data in the sample entry box " + found.kind.decode("latin-1"))
    if children is not None:
        for child in bmff.boxes(data, found.content, found.end):
            if child.kind not in children:
                raise unsupported("%s box in %s" % (child.kind.decode("latin-1"), found.kind.decode("latin-1")))
            check_entry_box(data, child, children[child.kind])


def profiles_in(data, entry, fields):
    """(start, end) of the ICC profile in each colr box of a sample entry."""
    for found in entry_boxes(data, entry, fields):
        if found.kind == b"colr" and data[found.content:found.content + 4] in (b"prof", b"rICC"):
            yield found.content + 4, found.end


def check_layout(data, found):
    """A box with a defined layout must have exactly its size."""
    size = found.end - found.content
    version = data[found.content] if size else 0
    expected = FIXED_SIZES.get(found.kind)
    if isinstance(expected, tuple):
        expected = expected[version] if version in (0, 1) else None
    elif found.kind in TABLES and size >= 8:
        before, entry = TABLES[found.kind]
        count = int.from_bytes(data[found.content + before:found.content + before + 4], "big")
        expected = before + 4 + count * entry[version] if version in (0, 1) else None
    elif found.kind == b"stsz" and size >= 12:
        fixed, count = struct.unpack_from(">II", data, found.content + 4)
        expected = 12 + (0 if fixed else 4 * count)
    elif found.kind not in FIXED_SIZES:
        return
    if size != expected:
        raise unsupported("extra data in the %s box" % found.kind.decode("latin-1"))


def cleared_fields(data, found):
    """The spans of a box that are cleared: creation and modification times,
    a handler's vendor code and name, data reference locations."""
    if found.kind in TIMED:
        size = 16 if data[found.content] == 1 else 8  # version 1 has 64-bit times
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


# ------------------------------------------------------------------------ media

def sample_tables(data, start, end):
    for found in bmff.boxes(data, start, end):
        if found.kind == b"stbl":
            yield found
        elif found.kind in (b"trak", b"mdia", b"minf"):
            yield from sample_tables(data, found.content, found.end)


def sample_sizes(data, stbl):
    """The size of each sample, from stsz."""
    parts = {found.kind: found.content for found in bmff.boxes(data, stbl.content, stbl.end)}
    try:
        fixed, count = struct.unpack_from(">II", data, parts[b"stsz"] + 4)
        return [fixed] * count if fixed else list(struct.unpack_from(">%dI" % count, data, parts[b"stsz"] + 12))
    except (KeyError, struct.error):
        raise unsupported("sample table")


def sample_ranges(data, stbl):
    """(start, end) of each chunk of samples, from stsc, stsz and stco/co64."""
    parts = {found.kind: found.content for found in bmff.boxes(data, stbl.content, stbl.end)}
    sizes = sample_sizes(data, stbl)
    try:
        runs = [struct.unpack_from(">III", data, parts[b"stsc"] + 8 + 12 * n)[:2]
                for n in range(struct.unpack_from(">I", data, parts[b"stsc"] + 4)[0])]
        wide = b"co64" in parts
        table = parts[b"co64" if wide else b"stco"]
        chunks = struct.unpack_from(">%d%s" % (struct.unpack_from(">I", data, table + 4)[0], "Q" if wide else "I"),
                                    data, table + 8)
    except (KeyError, struct.error):
        raise unsupported("sample table")
    # stsc runs: from chunk `first` on, each chunk holds `samples` samples.
    if runs and (runs[0][0] != 1 or any(a[0] >= b[0] for a, b in zip(runs, runs[1:]))):
        raise StructureError("invalid sample-to-chunk table")
    ranges, sample, run = [], 0, -1
    for index, offset in enumerate(chunks, 1):
        while run + 1 < len(runs) and runs[run + 1][0] <= index:
            run += 1
        per_chunk = runs[run][1] if run >= 0 else 0
        ranges.append((offset, offset + sum(sizes[sample:sample + per_chunk])))
        sample += per_chunk
    if sample != len(sizes):
        raise StructureError("sample table does not match its samples")
    return ranges


def movie_ranges(data, moov):
    """(start, end) of the chunks of samples of every track in a movie, sorted."""
    return sorted(span for table in sample_tables(data, moov.content, moov.end) for span in sample_ranges(data, table))


def gaps(used, start, end):
    """Parts of [start, end) that no used range covers; `used` is sorted."""
    position = start
    for a, b in used:
        if b <= position or a >= end:
            continue
        if a > position:
            yield position, a
        position = max(position, b)
    if position < end:
        yield position, end


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
    return found


def check_container(original, rebuilt, container, policy, track_handler):
    for found in bmff.boxes(rebuilt, container.content, container.end):
        if found.kind == b"free" and zeroed(rebuilt, [(found.content, found.end)]):
            continue
        if found.kind not in policy.boxes[container.kind]:
            fail("box %r kept" % found.kind)
        if rebuilt[found.start:found.content] != original[found.start:found.content]:
            fail("box %r changed" % found.kind)
        if found.kind in policy.boxes:
            check_container(original, rebuilt, found, policy,
                            handler(rebuilt, found) if found.kind == b"trak" else track_handler)
        elif found.kind == b"tref" and policy.references is not None:
            for reference in bmff.boxes(rebuilt, found.content, found.end):
                if not (reference.kind == b"free" and zeroed(rebuilt, [(reference.content, reference.end)])
                        or matches(original, rebuilt, reference, [])):
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
        if children and not matches(original, rebuilt, bmff.Box(b"", children[-1].end, children[-1].end, entry.end), []):
            fail("sample entry changed")
        sanitized = dict(profiles_in(original, entry, fields))
        for child in children:
            if child.kind == b"free" and zeroed(rebuilt, [(child.content, child.end)]) and not policy.strict:
                continue
            if child.kind not in kept:
                fail("sample entry box %r kept" % child.kind)
            check_entry_box(rebuilt, child, kept[child.kind])
            profile = sanitized.get(child.content + 4)
            if profile:
                expected = bytes(original[child.start:child.content + 4]) + icc.sanitize(
                    bytes(original[child.content + 4:profile]))
                if rebuilt[child.start:child.end] != expected:
                    fail("color profile not sanitized")
            elif not matches(original, rebuilt, child, []):
                fail("sample entry box %r changed" % child.kind)


def matches(original, rebuilt, found, spans):
    """Whether a box holds the original's bytes, but zeros in the given spans."""
    expected = bytearray(original[found.start:found.end])
    for start, end in spans:
        expected[start - found.start:end - found.start] = bytes(end - start)
    return rebuilt[found.start:found.end] == expected


def fail(detail):
    raise VerificationError("verification_failed", detail=detail)
