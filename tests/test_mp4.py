"""MP4 and QuickTime videos: tracks, boxes and media data, through rebuild,
verify and the whole pipeline. The files are built here, independently of the
code under test; never use personal videos in this suite."""
import os
import re
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from magicdispel import core, exiftool
from magicdispel.errors import FormatError, VerificationError
from magicdispel.formats import bmff, mp4

MARKER = b"MD_VIDEO_PRIVATE"
TIMES = struct.pack(">II", 3_800_000_000, 3_800_000_001)
VIDEO = [b"VIDEO_SAMPLE_ONE", b"VIDEO_SAMPLE_TWO"]
AUDIO = b"AUDIO_SAMPLE_ONE"
EXIFTOOL = exiftool.find()
SECOND_CHECK = EXIFTOOL if EXIFTOOL and exiftool.usable(exiftool.version(EXIFTOOL)) else None
HVCC = bytes([1, 1, 0x60, 0, 0, 0, 0x90, 0, 0, 0, 0, 0, 0x5D, 0xF0, 0, 0xFC, 0xFD, 0xF8, 0xF8, 0, 0, 0x0F, 0])
DVVC = bytes([1, 0, 0x10, 0x35]) + bytes(20)  # Dolby Vision 1.0, profile 8, level 6, RPU and base layer


def box(kind, payload):
    return struct.pack(">I", len(payload) + 8) + kind + payload


def full(kind, version, payload, flags=0):
    return box(kind, bytes([version]) + flags.to_bytes(3, "big") + payload)


def name(text):
    """A QuickTime name: a length byte and the text."""
    return bytes([len(text)]) + text


def handler(kind, text, component=b"mhlr"):
    return full(b"hdlr", 0, component + kind + b"appl" + bytes(8) + name(text))


def visual_entry(kind, boxes):
    fields = (bytes(6) + struct.pack(">H", 1) + bytes(4) + b"appl" + struct.pack(">II", 0, 512)
              + struct.pack(">HHIIIH", 64, 48, 0x480000, 0x480000, 0, 1) + name(MARKER).ljust(32, b"\0")
              + struct.pack(">Hh", 24, -1))
    return box(kind, fields + boxes + bytes(4))  # QuickTime's terminator


def descriptor(tag, body):
    """An MPEG-4 descriptor, its length in 4 bytes as Apple writes it."""
    return bytes([tag, 0x80, 0x80, 0x80, len(body)]) + body


def esds():
    """An AAC decoder configuration: the ES descriptor, the decoder's, AAC's
    own (LC, 48 kHz, stereo) and the sync layer's."""
    decoder = descriptor(4, bytes([0x40, 0x15]) + bytes(3) + struct.pack(">II", 128000, 128000)
                         + descriptor(5, bytes([0x11, 0x90])))
    return full(b"esds", 0, descriptor(3, struct.pack(">HB", 1, 0) + decoder + descriptor(6, b"\x02")))


def sound_entry(quicktime):
    if quicktime:  # version 1, with QuickTime's sound extension
        fields = (bytes(6) + struct.pack(">HHH", 1, 1, 0) + b"appl" + struct.pack(">HHhHI", 2, 16, -2, 0, 48000 << 16)
                  + struct.pack(">IIII", 1024, 0, 4, 2))
        wave = box(b"wave", box(b"frma", b"mp4a") + box(b"mp4a", bytes(4)) + esds()
                   + bytes(8))
        return box(b"mp4a", fields + wave)
    fields = bytes(6) + struct.pack(">H", 1) + bytes(8) + struct.pack(">HHHHI", 2, 16, 0, 0, 48000 << 16)
    return box(b"mp4a", fields + esds())


def table(offset, sizes):
    """Sample tables for one chunk of samples at `offset`."""
    return (full(b"stts", 0, struct.pack(">III", 1, len(sizes), 512))
            + full(b"stsc", 0, struct.pack(">IIII", 1, 1, len(sizes), 1))
            + full(b"stsz", 0, struct.pack(">II", 0, len(sizes)) + b"".join(struct.pack(">I", n) for n in sizes))
            + full(b"stco", 0, struct.pack(">II", 1, offset)))


def track(ident, kind, entry, offset, sizes, references=b"", extra=b"", header=b"vmhd"):
    media_header = {b"vmhd": full(b"vmhd", 0, bytes(8), flags=1), b"smhd": full(b"smhd", 0, bytes(4)),
                    b"gmhd": box(b"gmhd", MARKER), b"gmin": box(b"gmhd", full(b"gmin", 0, bytes(12)))}[header]
    minf = box(b"minf", media_header + handler(b"alis", b"Core Media Data Handler " + MARKER, b"dhlr")
               + box(b"dinf", full(b"dref", 0, struct.pack(">I", 1) + full(b"alis", 0, b"", flags=1)))
               + box(b"stbl", full(b"stsd", 0, struct.pack(">I", 1) + entry) + table(offset, sizes)))
    mdia = box(b"mdia", full(b"mdhd", 0, TIMES + struct.pack(">IIHH", 600, 1200, 0x55C4, 0))
               + handler(kind, b"Core Media " + MARKER) + minf)
    tkhd = full(b"tkhd", 0, TIMES + struct.pack(">III", ident, 0, 1200) + bytes(16) + struct.pack(
        ">9I", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000) + struct.pack(">II", 64 << 16, 48 << 16), flags=7)
    return box(b"trak", tkhd + (box(b"tref", references) if references else b"")
               + box(b"edts", full(b"elst", 0, struct.pack(">IIII", 1, 1200, 0, 0x10000))) + mdia + extra)


def movie_file(quicktime=True, typed=True, tracks=None, movie_extra=b"", top_extra=b"", fragmented=False, ftyp=None,
               meta=None):
    """A small video, QuickTime-style or MP4, with a video and a sound track,
    and metadata to remove: an Apple-style metadata track, a timecode track, a
    chapter track, user data, QuickTime metadata keys, a uuid box before mdat
    and Samsung-style SEF data and junk after moov. The movie box comes after
    the media, so it can be edited without moving any sample (see edited).
    `tracks` replaces the default tracks: [(id, handler, sample entry,
    [samples], references, media header)]; `meta`, the metadata box."""
    ftyp = ftyp or (box(b"ftyp", b"qt  \0\0\0\0qt  ") if quicktime else box(b"ftyp", b"mp42\0\0\0\0isommp42"))
    head = (ftyp if typed else b"") + box(b"wide", b"") + box(b"uuid", bytes(16) + MARKER) + top_extra
    samples = tracks or [
        (1, b"vide", visual_entry(b"hvc1", box(b"hvcC", HVCC) + box(b"colr", b"nclc" + struct.pack(">HHH", 9, 16, 9))
                                  + box(b"dvvC", DVVC) + box(b"pasp", struct.pack(">II", 1, 1))), VIDEO,
         box(b"tmcd", struct.pack(">I", 4)) + box(b"chap", struct.pack(">I", 5)), b"vmhd"),
        (2, b"soun", sound_entry(quicktime), [AUDIO], b"", b"smhd"),
        (3, b"meta", box(b"mebx", bytes(8) + box(b"keys", MARKER)), [b"FACE " + MARKER],
         box(b"cdsc", struct.pack(">I", 1)), b"gmhd"),
        (4, b"tmcd", box(b"tmcd", bytes(8) + MARKER), [b"\0\1\2\3"], b"", b"gmhd"),
        (5, b"text", box(b"text", bytes(8) + MARKER), [b"CHAPTER " + MARKER], b"", b"gmhd"),
    ]
    media, offsets, position = b"", [], len(head) + 8
    for _, _, _, sizes, _, _ in samples:
        offsets.append(position + len(media))
        media += b"".join(sizes) + MARKER  # unused bytes between chunks
    traks = b"".join(track(ident, kind, entry, offset, [len(sample) for sample in sizes], references,
                           header=media_header)
                     for (ident, kind, entry, sizes, references, media_header), offset in zip(samples, offsets))
    keys = full(b"keys", 0, struct.pack(">I", 1) + box(b"mdta", b"com.apple.quicktime.location.ISO6709"))
    ilst = box(b"ilst", box(struct.pack(">I", 1), box(b"data", struct.pack(">II", 1, 0) + b"+31.23+121.47/" + MARKER)))
    moov = box(b"moov", full(b"mvhd", 0, TIMES + struct.pack(">IIIH", 600, 1200, 0x10000, 0x100) + bytes(10)
                            + struct.pack(">9I", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000) + bytes(24)
                            + struct.pack(">I", 6))
               + traks + box(b"udta", box(b"\xa9xyz", MARKER))
               + (meta or box(b"meta", handler(b"mdta", b"") + keys + ilst)) + movie_extra)
    tail = box(b"moof", MARKER) if fragmented else b""
    return head + box(b"mdat", media) + moov + tail + box(b"sefd", MARKER) + b"TRAILING " + MARKER


def scene_illuminance(key=b"com.apple.quicktime.scene-illuminance", unit=b"com.apple.quicktime.milli-lux"):
    """Apple's scene illuminance sample entry, as iOS writes it: one key, its
    unit, a structural dependency and a conforming 32-bit unsigned type."""
    item = (box(b"keyd", b"mdta" + key) + box(b"dtyp", struct.pack(">I", 1) + unit)
            + box(b"sdpd", box(b"sdpi", bytes(4))) + box(b"ctps", box(b"dtyp", struct.pack(">II", 0, 77))))
    return box(b"mebx", bytes(6) + struct.pack(">H", 1) + box(b"keys", box(struct.pack(">I", 1), item)))


def illuminance(*millilux):
    return [struct.pack(">III", 12, 1, value) for value in millilux]


INTENT = b"com.apple.quicktime.full-frame-rate-playback-intent"


def apple_metadata(*items, handler_name=b"\0\0"):
    """A movie's metadata box as iPhones write it: an mdta handler, the keys,
    and an item of one value for each: (key, well-known type, value)."""
    keys = full(b"keys", 0, struct.pack(">I", len(items)) + b"".join(box(b"mdta", key) for key, _, _ in items))
    values = b"".join(box(struct.pack(">I", index), box(b"data", struct.pack(">II", kind, 0) + value))
                      for index, (_, kind, value) in enumerate(items, 1))
    return box(b"meta", full(b"hdlr", 0, bytes(4) + b"mdta" + bytes(12) + handler_name) + keys + box(b"ilst", values))


def intent(value, kind=21, size=8):
    return INTENT, kind, value.to_bytes(size, "big", signed=kind == 21)


PRIVATE_KEYS = [(b"com.apple.quicktime.make", 1, b"Apple " + MARKER), (b"com.apple.quicktime.creationdate", 1,
                 b"2026-09-26T03:19:09+0800"), (b"com.apple.quicktime.location.ISO6709", 1, b"+31.23+121.47/")]


def plain_video(boxes=box(b"avcC", bytes(7)), kind=b"avc1", references=b""):
    """A video of one track, and the movie's usual metadata."""
    return movie_file(tracks=[(1, b"vide", visual_entry(kind, boxes), VIDEO, references, b"vmhd")])


def boxes_at(data, path):
    """The boxes at a path of box types, such as [b"moov", b"trak"]."""
    found = [top for top in mp4.top_boxes(data) if top.kind == path[0]]
    for kind in path[1:]:
        found = [child for parent in found for child in bmff.boxes(data, parent.content, parent.end)
                 if child.kind == kind]
    return found


TRAK = [b"moov", b"trak"]
STBL = TRAK + [b"mdia", b"minf", b"stbl"]
CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"dinf", b"edts", b"tref"}


def edited(data, start, end, payload):
    """`data` with its bytes from start to end replaced by `payload`, and the
    boxes around them resized."""
    result = bytearray(data[:start] + payload + data[end:])
    parent = bmff.Box(b"", 0, 0, len(data))
    while parent := next((found for found in bmff.boxes(data, parent.content, parent.end)
                          if found.kind in CONTAINERS and found.content <= start and end <= found.end), None):
        struct.pack_into(">I", result, parent.start, parent.end - parent.start + len(payload) - (end - start))
    return bytes(result)


def replaced(data, old, new):
    start = data.index(old)
    return edited(data, start, start + len(old), new)


def inserted(data, path, payload):
    """`data` with `payload` at the end of the first box at a path of box
    types, which grows with the boxes it is in."""
    chain = [boxes_at(data, path[:depth])[0] for depth in range(1, len(path) + 1)]
    result = bytearray(data[:chain[-1].end] + payload + data[chain[-1].end:])
    for found in chain:
        struct.pack_into(">I", result, found.start, found.end - found.start + len(payload))
    return bytes(result)


def sound_movie(entry, durations, sizes):
    """A video with a sound track of four 4-byte frames in one chunk, whose
    stts is `durations` (after its count) and stsz `sizes`."""
    video = (1, b"vide", visual_entry(b"avc1", box(b"avcC", bytes(7))), VIDEO, b"", b"vmhd")
    data = movie_file(tracks=[video, (2, b"soun", entry, [b"PCM_", b"FRAM", b"ES_B", b"YTES"], b"", b"smhd")])
    data = replaced(data, full(b"stts", 0, struct.pack(">III", 1, 4, 512)), full(b"stts", 0, durations))
    return replaced(data, full(b"stsz", 0, struct.pack(">6I", 0, 4, 4, 4, 4, 4)), full(b"stsz", 0, sizes))


def pcm_entry(kind=b"sowt", channels=2, bits=16, packet=None):
    """A QuickTime sound entry: of version 0, or of version 1 with `packet`,
    its (frames, bytes)."""
    fields = struct.pack(">HHhHI", channels, bits, 0, 0, 48000 << 16)
    if packet:  # samples a packet, bytes a packet of one channel, of all of them, bytes a sample
        fields += struct.pack(">IIII", packet[0], packet[1] // channels, packet[1], 2)
    return box(kind, bytes(6) + struct.pack(">HHH", 1, 1 if packet else 0, 0) + b"appl" + fields)


class VideoTests(unittest.TestCase):
    def assertCleaned(self, data):
        rebuilt = mp4.rebuild(data)
        mp4.verify(data, rebuilt)
        self.assertNotIn(MARKER, rebuilt)
        return rebuilt

    def assertRefused(self, data, key="unsupported_part"):
        with self.assertRaises(FormatError) as caught:
            mp4.rebuild(data)
        self.assertEqual(caught.exception.key, key)


class MovieTests(VideoTests):

    def test_metadata_and_its_tracks_go_and_the_picture_and_sound_stay(self):
        for quicktime in (True, False):
            with self.subTest(quicktime=quicktime):
                data = movie_file(quicktime)
                rebuilt = self.assertCleaned(data)
                # Media data never moves: samples keep their places, and the rest of mdat is zero.
                mdat = boxes_at(rebuilt, [b"mdat"])[0]
                for sample in VIDEO + [AUDIO]:
                    self.assertEqual(rebuilt.find(sample), data.find(sample))
                self.assertEqual(rebuilt[mdat.content:mdat.end].replace(b"".join(VIDEO), b"").replace(AUDIO, b""),
                                 bytes(mdat.end - mdat.content - len(b"".join(VIDEO)) - len(AUDIO)))
                # Two tracks stay; the rest of the movie box is the same, but for zeros.
                self.assertEqual(len(boxes_at(rebuilt, [b"moov", b"trak"])), 2)
                self.assertEqual(len(rebuilt), data.index(b"sefd") - 4)
                for gone in (TIMES, b"appl", b"Core Media", b"+31.23"):
                    self.assertNotIn(gone, rebuilt)
                for kept in (HVCC, DVVC, b"nclc", b"wave" if quicktime else b"esds"):
                    self.assertIn(kept, rebuilt)
                # The video no longer names its timecode and chapters.
                references = boxes_at(rebuilt, [b"moov", b"trak", b"tref"])[0]
                self.assertEqual({child.kind for child in bmff.boxes(rebuilt, references.content, references.end)},
                                 {b"free"})

    def test_older_quicktime_movies_without_a_file_type_box_are_cleaned(self):
        data = movie_file(typed=False)
        self.assertEqual(mp4.brand_format(data), "MOV")
        self.assertCleaned(data)

    def test_verify_notices_a_tampered_result(self):
        data = movie_file()
        rebuilt = self.assertCleaned(data)
        udta = data.index(b"udta") - 4
        mdat = boxes_at(rebuilt, [b"mdat"])[0]
        tampered = [rebuilt[:udta + 4] + b"udta" + rebuilt[udta + 8:],  # a user data box back
                    rebuilt[:mdat.end - 16] + MARKER + rebuilt[mdat.end:],  # data in unused media
                    rebuilt.replace(VIDEO[1], VIDEO[1][::-1]),  # a sample changed
                    rebuilt + box(b"sefd", MARKER)]  # data after the movie
        for result in tampered:
            with self.subTest(size=len(result)), self.assertRaises(VerificationError):
                mp4.verify(data, result)

    def test_scene_illuminance_that_iphones_show_hdr_video_with_stays(self):
        video = (1, b"vide", visual_entry(b"hvc1", box(b"hvcC", HVCC)), VIDEO, b"", b"vmhd")
        lux = illuminance(69000, 68000, 100_000_000)
        data = movie_file(tracks=[video, (2, b"meta", scene_illuminance(), lux, box(b"rndr", struct.pack(">I", 1)),
                                          b"gmin")])
        rebuilt = self.assertCleaned(data)
        self.assertEqual(len(boxes_at(rebuilt, [b"moov", b"trak"])), 2)
        self.assertIn(scene_illuminance(), rebuilt)
        self.assertEqual(rebuilt.find(b"".join(lux)), data.find(b"".join(lux)))
        # Anything else Apple might render with is refused, as are values out of range.
        variants = {
            "another key": scene_illuminance(key=b"com.apple.quicktime.scene-illuminancf"),
            "another unit": scene_illuminance(unit=b"com.apple.quicktime.micro-lux"),
        }
        for reason, entry in variants.items():
            with self.subTest(reason):
                self.assertRefused(movie_file(tracks=[video, (2, b"meta", entry, lux, box(b"rndr", struct.pack(">I", 1)),
                                                               b"gmin")]))
        for reason, samples in {"out of range": illuminance(100_000_001), "long": [lux[0] + bytes(4)],
                                "another key": [struct.pack(">III", 12, 2, 1)]}.items():
            with self.subTest(reason):
                self.assertRefused(movie_file(tracks=[video, (2, b"meta", scene_illuminance(), samples,
                                                              box(b"rndr", struct.pack(">I", 1)), b"gmin")]))
        # Without the reference it shows nothing, and goes like any metadata track.
        plain = self.assertCleaned(movie_file(tracks=[video, (2, b"meta", scene_illuminance(), lux, b"", b"gmin")]))
        self.assertEqual(len(boxes_at(plain, [b"moov", b"trak"])), 1)

    def test_a_fast_videos_playback_intent_is_the_only_metadata_kept(self):
        # Since iOS 18, whether a video of 120 fps or more plays at its full
        # rate (1) or in slow motion (0), which some players decide by.
        for value, kind, size in ((1, 21, 8), (0, 21, 8), (1, 22, 1), (1, 21, 4)):
            with self.subTest(value=value, kind=kind, size=size):
                data = movie_file(meta=apple_metadata(*PRIVATE_KEYS[:2], intent(value, kind, size), PRIVATE_KEYS[2]))
                rebuilt = self.assertCleaned(data)
                self.assertNotIn(b"+31.23", rebuilt)
                self.assertNotIn(b"2026-09-26", rebuilt)
                # Rewritten as iPhones write it, in the space the metadata had, and a free box after it.
                meta = boxes_at(data, [b"moov", b"meta"])[0]
                self.assertEqual(rebuilt[meta.start:meta.end], mp4.playback_metadata(value) + struct.pack(
                    ">I", meta.end - meta.start - len(mp4.playback_metadata(value))) + b"free"
                    + bytes(meta.end - meta.start - len(mp4.playback_metadata(value)) - 8))
                self.assertEqual(mp4.rebuild(rebuilt), rebuilt)  # a clean copy cleans to itself
                if SECOND_CHECK:
                    tags = exiftool.read(SECOND_CHECK, rebuilt)
                    intents = [found for key, found in tags.items() if key.endswith(":FullFrameRatePlaybackIntent")]
                    self.assertEqual(intents, [value])
                    exiftool.check_tags(tags, "MOV")
        # Another value, or data in the free box after it, fails verification.
        data = movie_file(meta=apple_metadata(intent(1), *PRIVATE_KEYS))
        rebuilt = self.assertCleaned(data)
        after = rebuilt.index(mp4.playback_metadata(1)) + len(mp4.playback_metadata(1)) + 8
        for result in (rebuilt.replace(mp4.playback_metadata(1), mp4.playback_metadata(0)),
                       rebuilt[:after] + MARKER + rebuilt[after + len(MARKER):]):
            with self.subTest(size=len(result)), self.assertRaises(VerificationError):
                mp4.verify(data, result)

    def test_other_playback_intents_go_with_the_metadata(self):
        dropped = {
            "no intent": apple_metadata(intent(2), *PRIVATE_KEYS),
            "as text": apple_metadata((INTENT, 1, b"1"), *PRIVATE_KEYS),
            "a float": apple_metadata((INTENT, 23, struct.pack(">f", 1)), *PRIVATE_KEYS),
            "three bytes": apple_metadata(intent(1, size=3), *PRIVATE_KEYS),
            "twice": apple_metadata(intent(1), intent(1), *PRIVATE_KEYS),
            "two values": box(b"meta", full(b"hdlr", 0, bytes(4) + b"mdta" + bytes(14))
                              + full(b"keys", 0, struct.pack(">I", 1) + box(b"mdta", INTENT))
                              + box(b"ilst", box(struct.pack(">I", 1), box(b"data", struct.pack(">IIq", 21, 0, 1)) * 2))
                              + box(b"free", bytes(64))),
            "another handler": apple_metadata(intent(1), *PRIVATE_KEYS).replace(b"mdta" + bytes(14),
                                                                                b"mdir" + bytes(14)),
            # With a version, as ISO metadata boxes have: not as Apple writes them.
            "a version": box(b"meta", bytes(4) + apple_metadata(intent(1), *PRIVATE_KEYS)[8:]),
            # The rewrite and a free box do not fit: 4 bytes are left.
            "no room": apple_metadata(intent(1), handler_name=bytes(6)),
            # More keys than any writer uses, made to exhaust memory: not read.
            "too many keys": apple_metadata(intent(1), *[(b"k", 1, b"")] * 256),
        }
        for reason, meta in dropped.items():
            with self.subTest(reason):
                rebuilt = self.assertCleaned(movie_file(meta=meta))
                self.assertEqual(boxes_at(rebuilt, [b"moov", b"meta"]), [])
        # Two metadata boxes: neither is kept.
        twice = movie_file(meta=apple_metadata(intent(1)), movie_extra=apple_metadata(intent(1), *PRIVATE_KEYS))
        self.assertEqual(boxes_at(self.assertCleaned(twice), [b"moov", b"meta"]), [])
        # One holding just the intent, as a clean copy does, is kept as it is.
        exact = movie_file(meta=mp4.playback_metadata(1))
        self.assertIn(mp4.playback_metadata(1), self.assertCleaned(exact))

    def test_what_cannot_be_cleaned_safely_is_refused(self):
        video = (1, b"vide", visual_entry(b"avc1", box(b"avcC", bytes(7))), VIDEO, b"", b"vmhd")
        refused = {
            "fragmented": movie_file(fragmented=True),
            "subtitles": movie_file(tracks=[video, (2, b"sbtl", box(b"tx3g", bytes(8)), [b"HELLO"], b"", b"gmhd")]),
            "unknown entry box": movie_file(tracks=[
                (1, b"vide", visual_entry(b"avc1", box(b"avcC", bytes(7)) + box(b"prvt", MARKER)), VIDEO, b"", b"vmhd")]),
            "unknown entry": movie_file(tracks=[(1, b"vide", visual_entry(b"jpeg", b""), VIDEO, b"", b"vmhd")]),
            "extra bytes": movie_file(tracks=[
                (1, b"vide", visual_entry(b"avc1", box(b"avcC", bytes(7)) + box(b"pasp", bytes(12))), VIDEO, b"",
                 b"vmhd")]),
            "needed to show": movie_file(tracks=[video, (2, b"meta", box(b"it35", bytes(8)), [b"HDR"],
                                                         box(b"rndr", struct.pack(">I", 1)), b"gmhd")]),
            "depends on removed": movie_file(tracks=[
                (1, b"vide", visual_entry(b"avc1", box(b"avcC", bytes(7))), VIDEO, box(b"vdep", struct.pack(">I", 2)),
                 b"vmhd"), (2, b"meta", box(b"mebx", bytes(8)), [b"DEPTH"], b"", b"gmhd")]),
        }
        for reason, data in refused.items():
            with self.subTest(reason):
                self.assertRefused(data)
        self.assertRefused(movie_file(tracks=[(1, b"soun", sound_entry(False), [AUDIO], b"", b"smhd")]), "no_video")
        external = movie_file(tracks=[video]).replace(b"alis\0\0\0\1", b"alis\0\0\0\0")
        self.assertRefused(external)
        # Samples outside any media data box: a damaged or truncated file.
        moved = movie_file(tracks=[video])
        mdat = moved.index(b"mdat")
        self.assertRefused(moved[:mdat] + b"free" + moved[mdat + 4:], "damaged")


class TableTests(VideoTests):
    """A kept track's boxes must be what players read, and agree with each other."""

    def test_tables_that_disagree_are_damage(self):
        data = plain_video()
        stts, stsc = full(b"stts", 0, struct.pack(">III", 1, 2, 512)), full(b"stsc", 0, struct.pack(">IIII", 1, 1, 2, 1))
        stco = boxes_at(data, STBL + [b"stco"])[0]
        tkhd = boxes_at(data, TRAK + [b"tkhd"])[0]
        damaged = {
            "more timed samples": replaced(data, stts, full(b"stts", 0, struct.pack(">III", 1, 3, 512))),
            "no time-to-sample table": replaced(data, stts, b""),
            "a second description": replaced(data, stsc, full(b"stsc", 0, struct.pack(">IIII", 1, 1, 2, 2))),
            "a sync sample past the end": inserted(data, STBL, full(b"stss", 0, struct.pack(">II", 1, 3))),
            "offsets for more samples": inserted(data, STBL, full(b"ctts", 0, struct.pack(">III", 1, 3, 0))),
            "two chunk offset tables": inserted(data, STBL, data[stco.start:stco.end]),
            "32- and 64-bit offsets": inserted(data, STBL, full(b"co64", 0, struct.pack(">IQ", 1, 0))),
            "two track headers": inserted(data, TRAK, data[tkhd.start:tkhd.end]),
            "offsets outside the sample table": inserted(data, TRAK + [b"mdia", b"minf"], data[stco.start:stco.end]),
            "an empty time-to-sample entry": replaced(data, stts,
                                                      full(b"stts", 0, struct.pack(">5I", 2, 0, 9, 2, 512))),
            "chunks of no samples": replaced(replaced(replaced(data, stts, full(b"stts", 0, bytes(4))), stsc, full(
                b"stsc", 0, bytes(4))), full(b"stsz", 0, struct.pack(">4I", 0, 2, 16, 16)), full(b"stsz", 0, bytes(8))),
        }
        for reason, broken in damaged.items():
            with self.subTest(reason):
                self.assertRefused(broken, "damaged")
        offset = int.from_bytes(data[stco.content + 8:stco.end], "big")
        one_a_chunk = replaced(data, stsc, full(b"stsc", 0, struct.pack(">IIII", 1, 1, 1, 1)))
        refused = {
            "a chunk used twice": replaced(one_a_chunk, data[stco.start:stco.end],
                                           full(b"stco", 0, struct.pack(">III", 2, offset, offset + 8))),
            "an empty chunk": replaced(replaced(one_a_chunk, data[stco.start:stco.end], full(
                b"stco", 0, struct.pack(">III", 2, offset, offset + 32))), full(b"stsz", 0, struct.pack(
                    ">4I", 0, 2, 16, 16)), full(b"stsz", 0, struct.pack(">4I", 0, 2, 16, 0))),
            "flags in stsz": replaced(data, full(b"stsz", 0, struct.pack(">4I", 0, 2, 16, 16)),
                                      full(b"stsz", 0, struct.pack(">4I", 0, 2, 16, 16), flags=1)),
            "a second version of stts": replaced(data, stts, full(b"stts", 1, struct.pack(">III", 1, 2, 512))),
        }
        for reason, broken in refused.items():
            with self.subTest(reason):
                self.assertRefused(broken)
        # An edit list may repeat, as animations loop.
        elst = full(b"elst", 0, struct.pack(">IIIII", 1, 1200, 0, 0x10000, 0)[:16])
        self.assertCleaned(replaced(data, elst, full(b"elst", 0, elst[12:], flags=1)))

    def test_tables_claiming_billions_of_samples_are_refused_at_once(self):
        data, billions = plain_video(), 0xFFFFFFFF
        huge = replaced(data, full(b"stts", 0, struct.pack(">III", 1, 2, 512)),
                        full(b"stts", 0, struct.pack(">III", 1, billions, 1)))
        huge = replaced(huge, full(b"stsc", 0, struct.pack(">IIII", 1, 1, 2, 1)),
                        full(b"stsc", 0, struct.pack(">IIII", 1, 1, billions, 1)))
        huge = replaced(huge, full(b"stsz", 0, struct.pack(">4I", 0, 2, 16, 16)),
                        full(b"stsz", 0, struct.pack(">II", 16, billions)))
        self.assertRefused(huge, "damaged")  # samples past the end of the file
        self.assertRefused(data.replace(b"stsd\0\0\0\0\0\0\0\1", b"stsd\0\0\0\0" + struct.pack(">I", billions)),
                           "damaged")

    def test_sample_groups_stay_only_when_every_byte_is_checked(self):
        def sgpd(kind, descriptions, size, version=1, default=0):
            return full(b"sgpd", version, kind + struct.pack(">I", size) + (struct.pack(">I", default) if version == 2
                                                                             else b"")
                        + struct.pack(">I", len(descriptions)) + b"".join(descriptions))

        def sbgp(kind, samples=2, index=1, parameter=None):
            return full(b"sbgp", 0 if parameter is None else 1, kind + (b"" if parameter is None else parameter)
                        + struct.pack(">III", 1, samples, index))

        roll = sgpd(b"roll", [struct.pack(">h", -1), b"MD"], 2) + sbgp(b"roll")
        data = inserted(plain_video(), STBL, roll + sgpd(b"prvt", [MARKER], 16) + sbgp(b"prvt"))
        rebuilt = self.assertCleaned(data)
        self.assertIn(roll.replace(b"MD", bytes(2)), rebuilt)  # a description no sample uses is cleared
        self.assertNotIn(b"prvt", rebuilt)
        layers = sgpd(b"sync", [b"\x13"], 1)  # one description and no groups, as Apple writes temporal layers
        self.assertIn(layers, self.assertCleaned(inserted(plain_video(), STBL, layers)))
        refused = {
            "another size": sgpd(b"roll", [bytes(3)], 3) + sbgp(b"roll"),
            "past the descriptions": sgpd(b"roll", [bytes(2)], 2) + sbgp(b"roll", index=2),
            "past the samples": sgpd(b"roll", [bytes(2)], 2) + sbgp(b"roll", samples=3),
            "a group of no samples": sgpd(b"roll", [bytes(2)], 2) + sbgp(b"roll", samples=0),
            "reserved bits": sgpd(b"sync", [b"\xc1"], 1) + sbgp(b"sync"),
            "a grouping parameter": sgpd(b"roll", [bytes(2)], 2) + sbgp(b"roll", parameter=b"MDPV"),
            "a default past the descriptions": sgpd(b"roll", [bytes(2)], 2, version=2, default=2) + sbgp(b"roll"),
            "descriptions without groups": sgpd(b"sync", [b"\x13", b"\x14"], 1),
            "a reserved dependency": full(b"sdtp", 0, bytes([0x30, 0x10])),
        }
        for reason, boxes in refused.items():
            with self.subTest(reason):
                self.assertRefused(inserted(plain_video(), STBL, boxes))
        self.assertCleaned(inserted(plain_video(), STBL, full(b"sdtp", 0, bytes([0x20, 0x10]))))
        self.assertRefused(inserted(plain_video(), STBL, roll + roll), "damaged")
        for kind in (b"stsh", b"subs", b"padb"):  # seeking and decoding hints no player needs
            with self.subTest(kind):
                self.assertCleaned(inserted(plain_video(), STBL, full(kind, 0, struct.pack(">I", 1) + MARKER)))

    def test_uncompressed_sound_is_read_by_its_sample_entry(self):
        # Sound whose table counts frames of one time unit, each of "size 1":
        # players read each chunk as frames times their size in the entry.
        frames, one_unit = struct.pack(">II", 1, 4), struct.pack(">III", 1, 4, 1)
        for sizes in (frames, struct.pack(">II", 4, 4)):
            with self.subTest(sizes=sizes):
                data = sound_movie(pcm_entry(), one_unit, sizes)
                self.assertEqual(self.assertCleaned(data).find(b"PCM_FRAMES_BYTES"), data.find(b"PCM_FRAMES_BYTES"))
        # ISO's float sound: the bits per sample are in pcmC, whatever the entry says.
        fpcm = box(b"fpcm", bytes(6) + struct.pack(">H", 1) + bytes(8) + struct.pack(">HHHHI", 1, 16, 0, 0, 48000 << 16)
                   + full(b"pcmC", 0, bytes([1, 32])))
        data = sound_movie(fpcm, one_unit, struct.pack(">II", 4, 4))
        self.assertEqual(self.assertCleaned(data).find(b"PCM_FRAMES_BYTES"), data.find(b"PCM_FRAMES_BYTES"))
        # u-law as AVFoundation writes it: an entry of version 2, a frame (of 4 channels here) a packet.
        ulaw = box(b"ulaw", bytes(6) + struct.pack(">HHH", 1, 2, 0) + b"appl" + struct.pack(">HHhHI", 3, 16, -2, 0, 1 << 16)
                   + struct.pack(">IdIIIIII", 72, 48000.0, 4, 0x7F000000, 8, 0, 4, 1))
        data = sound_movie(ulaw, one_unit, frames)
        self.assertEqual(self.assertCleaned(data).find(b"PCM_FRAMES_BYTES"), data.find(b"PCM_FRAMES_BYTES"))
        # Packets of several frames are read whole, and frames by their channels and bits.
        whole = sound_movie(pcm_entry(channels=2, packet=(2, 8)), one_unit, frames)
        self.assertEqual(self.assertCleaned(whole).find(b"PCM_FRAMES_BYTES"), whole.find(b"PCM_FRAMES_BYTES"))
        # Sound that players could read in two ways, or by sizes the table does not give.
        ipcm = box(b"ipcm", bytes(6) + struct.pack(">H", 1) + bytes(8) + struct.pack(">HHHHI", 1, 16, 0, 0, 48000 << 16)
                   + full(b"pcmC", 0, bytes([1, 16])))
        refused = {
            "packets of another size than a frame": sound_movie(pcm_entry(channels=1, packet=(1, 8)), one_unit, frames),
            "chunks of part of a packet": sound_movie(pcm_entry(channels=1, packet=(3, 6)), one_unit, frames),
            "packets of other frames than the entry's": sound_movie(pcm_entry(channels=1, packet=(2, 8)), one_unit,
                                                                    frames),
            "IMA ADPCM of other packets than its own": sound_movie(pcm_entry(b"ima4", 2, 16, packet=(64, 34)),
                                                                   one_unit, frames),
            "ISO's, which FFmpeg reads by stsz": sound_movie(ipcm, one_unit, struct.pack(">II", 4, 4)),
            "raw of 24 bits, which FFmpeg reads as 8": sound_movie(pcm_entry(b"raw ", 1, 24), one_unit, frames),
            "twos of 64 bits, which FFmpeg reads as 16": sound_movie(pcm_entry(b"twos", 1, 64), one_unit, frames),
            "IMA ADPCM of part of a packet": sound_movie(pcm_entry(b"ima4", 1, 16), one_unit, frames),
            "compressed, of size 1": sound_movie(sound_entry(False), one_unit, frames),
            "one-unit frames in two runs": sound_movie(pcm_entry(), struct.pack(">5I", 2, 2, 1, 2, 1), frames),
            "one-unit frames of listed sizes": sound_movie(pcm_entry(), one_unit, struct.pack(">6I", 0, 4, 1, 1, 1, 1)),
            "frames of 12 bits": sound_movie(pcm_entry(bits=12), one_unit, frames),
            "frames of another size": sound_movie(pcm_entry(), struct.pack(">III", 1, 4, 2), struct.pack(">II", 2, 4)),
        }
        for reason, data in refused.items():
            with self.subTest(reason):
                self.assertRefused(data)
        data = sound_movie(pcm_entry(), one_unit, frames)
        stsd = boxes_at(data, STBL + [b"stsd"])[1]  # the sound track's
        self.assertRefused(data[:stsd.content] + b"\1" + data[stsd.content + 1:])  # FFmpeg reads version 1 by brand
        self.assertRefused(replaced(data, data[stsd.start:stsd.end], full(b"stsd", 0, bytes(4))), "damaged")

    def test_the_file_type_box_keeps_only_brands_that_say_how_to_read_it(self):
        nikon = box(b"ftyp", b"qt  \x20\x07\x09\x00qt  niko")
        self.assertTrue(self.assertCleaned(movie_file(ftyp=nikon)).startswith(nikon.replace(b"niko", bytes(4))))
        m4v = box(b"ftyp", b"M4V \0\0\0\1M4V M4A isommp42")  # as Apple's exports write it
        self.assertTrue(self.assertCleaned(movie_file(False, ftyp=m4v)).startswith(m4v))
        padded = box(b"ftyp", b"qt  \x20\x05\x03\x00qt  " + bytes(8))  # as older QuickTime writes it
        self.assertTrue(self.assertCleaned(movie_file(ftyp=padded)).startswith(padded))
        # A second file type box is no file type box.
        self.assertCleaned(movie_file(top_extra=box(b"ftyp", b"isom\0\0\0\0" + MARKER)))
        self.assertCleaned(movie_file(typed=False, top_extra=box(b"ftyp", b"qt  \0\0\0\0" + MARKER)))
        for ftyp in (box(b"ftyp", b"MDPV\0\0\0\0isom"), box(b"ftyp", b"isom\0\0\0\0" + b"isom" * 64)):
            with self.subTest(ftyp[8:12]):
                self.assertRefused(movie_file(False, ftyp=ftyp))

    def test_the_decoder_configuration_holds_nothing_after_it(self):
        self.assertCleaned(plain_video(box(b"avcC", bytes([1, 100, 0, 30, 0xFF, 0xE1, 0, 2]) + b"SP" + bytes([1, 0, 1])
                                           + b"P" + bytes([0xFD, 0xF8, 0xF8, 0]))))  # a high profile's extension
        video = (1, b"vide", visual_entry(b"avc1", box(b"avcC", bytes(7))), VIDEO, b"", b"vmhd")
        streaminfo = bytes([0x80]) + (34).to_bytes(3, "big") + bytes(34)
        fields = bytes(6) + struct.pack(">H", 1) + bytes(8) + struct.pack(">HHHHI", 2, 16, 0, 0, 48000 << 16)

        def sound(entry):
            return movie_file(tracks=[video, (2, b"soun", entry, [AUDIO], b"", b"smhd")])

        self.assertCleaned(sound(box(b"fLaC", fields + full(b"dfLa", 0, streaminfo))))
        self.assertCleaned(sound(box(b"Opus", fields + box(b"dOps", bytes([0, 2]) + bytes(9)))))
        # x264's lossless and 4:4:4 profile, whose avcC gives the chroma format and bit depths too.
        self.assertCleaned(plain_video(box(b"avcC", bytes([1, 244, 0, 30, 0xFF, 0xE1, 0, 2]) + b"SP" + bytes([1, 0, 1])
                                           + b"P" + bytes([0xFF, 0xF8, 0xF8, 0]))))
        # QuickTime's sound extension, holding Dolby's and FLAC's configurations.
        quicktime = (bytes(6) + struct.pack(">HHH", 1, 1, 0) + b"appl" + struct.pack(">HHhHI", 2, 16, -2, 0, 48000 << 16)
                     + struct.pack(">IIII", 1536, 0, 0, 2))
        for kind, configuration in ((b"ac-3", box(b"dac3", bytes.fromhex("1008c0"))),
                                    (b"ec-3", box(b"dec3", bytes.fromhex("0300200200"))),
                                    (b"flac", full(b"dfLa", 0, streaminfo))):
            with self.subTest(kind):
                self.assertCleaned(sound(box(kind, quicktime + box(b"wave", box(b"frma", kind) + configuration
                                                                  + bytes(8)))))
        # AMR's configuration in QuickTime's wave: the codec maker's name is cleared there too.
        amr = sound(box(b"samr", quicktime + box(b"wave", box(b"frma", b"samr") + box(b"samr", b"VNDR" + bytes(5))
                                                  + bytes(8))))
        self.assertNotIn(b"VNDR", self.assertCleaned(amr))
        # ISO sound's channel layout, and Apple's positional audio, whose configuration is copied whole.
        ipcm = box(b"ipcm", fields + full(b"pcmC", 0, bytes([1, 16])) + full(b"chnl", 0, bytes([1, 2]) + bytes(8)))
        self.assertCleaned(sound_movie(ipcm, struct.pack(">III", 1, 4, 1), struct.pack(">II", 4, 4)))
        self.assertCleaned(sound(box(b"apac", fields + full(b"dapa", 0, bytes(8)))))
        comment = bytes([0x84]) + (len(MARKER)).to_bytes(3, "big") + MARKER
        refused = {
            "after avcC": plain_video(box(b"avcC", bytes(7) + MARKER)),
            "after hvcC": plain_video(box(b"hvcC", HVCC + MARKER), b"hvc1"),
            "after esds": sound(box(b"mp4a", fields + esds() + box(b"udta", MARKER))),
            "inside esds": sound(box(b"mp4a", fields + box(b"esds", esds()[8:] + MARKER))),
            "FLAC tags": sound(box(b"fLaC", fields + full(b"dfLa", 0, streaminfo[:1].replace(b"\x80", b"\0")
                                                            + streaminfo[1:] + comment))),
            "after dOps": sound(box(b"Opus", fields + box(b"dOps", bytes([0, 2]) + bytes(9) + MARKER))),
            "a longer stream information": sound(box(b"fLaC", fields + full(b"dfLa", 0, bytes([0x80]) + (
                34 + len(MARKER)).to_bytes(3, "big") + bytes(34) + MARKER))),
            "flags in vpcC": plain_video(box(b"vpcC", b"\1XYZ" + bytes(8)), b"vp09"),
            "a speaker of no known position": sound_movie(box(b"ipcm", fields + full(b"pcmC", 0, bytes([1, 16]))
                                                               + full(b"chnl", 0, bytes([1, 0, 0x41, 0x41]))),
                                                           struct.pack(">III", 1, 4, 1), struct.pack(">II", 4, 4)),
        }
        for reason, data in refused.items():
            with self.subTest(reason):
                self.assertRefused(data)

    def test_fields_no_reader_uses_are_cleared(self):
        data = plain_video()
        mvhd, tkhd = boxes_at(data, [b"moov", b"mvhd"])[0], boxes_at(data, TRAK + [b"tkhd"])[0]
        planted = bytearray(data)
        planted[mvhd.content + 72:mvhd.content + 88] = MARKER  # QuickTime's poster and current times
        planted[tkhd.content + 24:tkhd.content + 32] = MARKER[:8]  # reserved
        entry = boxes_at(data, STBL + [b"stsd"])[0].content + 8
        planted[entry + 8:entry + 14] = b"SECRET"  # the reserved bytes of the sample entry
        mdhd = boxes_at(data, TRAK + [b"mdia", b"mdhd"])[0]
        planted[mdhd.end - 2:mdhd.end] = b"Q!"  # ISO's pre_defined, QuickTime's quality
        rebuilt = self.assertCleaned(bytes(planted))
        self.assertNotIn(MARKER[:8], rebuilt)
        self.assertNotIn(b"SECRET", rebuilt)
        self.assertNotIn(b"Q!", rebuilt)
        self.assertRefused(data.replace(b"alis\0\0\0\1", b"alis\0\0\0\3"))  # flags of no known meaning

    def test_sound_entries_hold_only_their_values(self):
        video = (1, b"vide", visual_entry(b"avc1", box(b"avcC", bytes(7))), VIDEO, b"", b"vmhd")
        fields = bytes(6) + struct.pack(">H", 1) + bytes(8) + struct.pack(">HHHHI", 2, 16, 0, 0, 48000 << 16)

        def sound(entry):
            return movie_file(tracks=[video, (2, b"soun", entry, [AUDIO], b"", b"smhd")])

        def ipcm(chnl):
            entry = box(b"ipcm", fields + full(b"pcmC", 0, bytes([1, 16])) + full(b"chnl", 0, chnl))
            return sound_movie(entry, struct.pack(">III", 1, 4, 1), struct.pack(">II", 4, 4))

        self.assertCleaned(ipcm(bytes([1, 0, 1, 2])))  # a speaker position for each of the 2 channels
        quicktime = (bytes(6) + struct.pack(">HHH", 1, 1, 0) + b"appl" + struct.pack(">HHhHI", 2, 16, -2, 0, 48000 << 16)
                     + struct.pack(">IIII", 1024, 0, 0, 2))
        ulaw = box(b"ulaw", bytes(6) + struct.pack(">HHH", 1, 2, 0) + b"appl" + struct.pack(">HHhHI", 3, 16, -2, 0, 1 << 16)
                   + struct.pack(">IdIIIIII", 72, 48000.0, 4, 0x7F000000, 8, 0, 4, 1))
        lpcm = box(b"lpcm", ulaw[8:52] + struct.pack(">IIIII", 0x7F000000, 24, 1, 12, 1))  # 4 channels of 24-bit floats
        refused = {
            "more speakers than channels": ipcm(bytes([1, 0, 1, 2]) + b"+31.2304+121.4737/"),
            "channels left out": ipcm(bytes([1, 2]) + MARKER[:8]),
            "a constant of another value": sound_movie(box(b"ulaw", ulaw[8:24] + b"\0\4" + ulaw[26:]), struct.pack(
                ">III", 1, 4, 1), struct.pack(">II", 1, 4)),
            "floats of 24 bits": sound_movie(lpcm, struct.pack(">III", 1, 4, 1), struct.pack(">II", 1, 4)),
            "a channel described with coordinates it does not use": sound(box(b"mp4a", fields + esds() + full(
                b"chan", 0, struct.pack(">III", 0, 0, 1) + struct.pack(">II", 1, 0) + MARKER[:12]))),
            "a channel layout tag of no kind": sound(box(b"mp4a", fields + esds() + full(b"chan", 0, b"MDTM" + bytes(8)))),
            "another format in QuickTime's sound extension": sound(box(b"mp4a", quicktime + box(b"wave", box(
                b"frma", b"MDTM") + box(b"mp4a", bytes(4)) + esds() + bytes(8)))),
            "a byte order of no meaning": sound(box(b"mp4a", quicktime + box(b"wave", box(b"frma", b"mp4a") + box(
                b"enda", b"MD") + esds() + bytes(8)))),
            "a sample rate of no number": sound_movie(box(b"ulaw", ulaw[8:40] + struct.pack(">d", float("nan"))
                                                          + ulaw[48:]), struct.pack(">III", 1, 4, 1),
                                                      struct.pack(">II", 1, 4)),
            "channels not interleaved": sound_movie(box(b"lpcm", ulaw[8:52] + struct.pack(">IIIII", 0x7F000000, 8, 0x2C,
                                                                                         4, 1)),
                                                    struct.pack(">III", 1, 4, 1), struct.pack(">II", 4, 4)),
        }
        for reason, data in refused.items():
            with self.subTest(reason):
                self.assertRefused(data)

    def test_channel_layouts_as_apple_writes_them(self):
        video = (1, b"vide", visual_entry(b"avc1", box(b"avcC", bytes(7))), VIDEO, b"", b"vmhd")
        fields = bytes(6) + struct.pack(">H", 1) + bytes(8) + struct.pack(">HHHHI", 2, 16, 0, 0, 48000 << 16)

        def layout(tag, count=0, labels=(), extra=b""):
            descriptions = b"".join(struct.pack(">II", label, 0) + bytes(12) for label in labels) + extra
            entry = box(b"mp4a", fields + esds() + full(b"chan", 0, struct.pack(">III", tag, 0, count) + descriptions))
            return movie_file(tracks=[video, (2, b"soun", entry, [AUDIO], b"", b"smhd")])

        for reason, data in {"an unknown layout of 2 channels": layout(0xFFFF << 16 | 2),
                             "headphones": layout(0, 2, (301, 302)),
                             "two discrete channels": layout(0, 2, (0x10000, 0x10001))}.items():
            with self.subTest(reason):
                self.assertCleaned(data)
        for reason, data in {"a layout of 3 channels for 2": layout(101 << 16 | 3),
                             "a description too many": layout(0, 3, (1, 2, 3)),
                             "coordinates": layout(0, 2, (1,), struct.pack(">II", 2, 1) + bytes(12))}.items():
            with self.subTest(reason):
                self.assertRefused(data)
        # Parametric immersive video (macOS 26) keeps its projection.
        prim = box(b"vexu", box(b"proj", full(b"prji", 0, b"prim")))
        self.assertCleaned(plain_video(box(b"hvcC", HVCC) + prim, b"hvc1"))

    def test_chapter_pictures_go_with_the_chapters(self):
        video = (1, b"vide", visual_entry(b"avc1", box(b"avcC", bytes(7))), VIDEO,
                 box(b"chap", struct.pack(">II", 2, 3)), b"vmhd")
        text = (2, b"text", box(b"text", bytes(8) + MARKER), [b"CHAPTER " + MARKER], b"", b"gmhd")
        pictures = (3, b"vide", visual_entry(b"jpeg", b""), [b"JPEG " + MARKER], b"", b"vmhd")
        data = movie_file(tracks=[video, text, pictures])
        tkhd = boxes_at(data, TRAK + [b"tkhd"])[2]
        disabled = data[:tkhd.content + 3] + b"\0" + data[tkhd.content + 4:]  # the track is not enabled
        self.assertEqual(len(boxes_at(self.assertCleaned(disabled), TRAK)), 1)
        self.assertRefused(data)  # an enabled track of pictures is a video of a codec not on the list

    def test_a_compressed_movie_header_is_refused_as_such(self):
        data = inserted(plain_video(), [b"moov"], box(b"cmov", box(b"dcom", b"zlib")))
        with self.assertRaises(FormatError) as caught:
            mp4.rebuild(data)
        self.assertIn("compressed movie header", str(caught.exception))

    def test_what_players_do_not_need_goes(self):
        rebuilt = self.assertCleaned(inserted(plain_video(), [b"moov"], box(b"mvex", full(b"trex", 0, bytes(20)))))
        self.assertNotIn(b"trex", rebuilt)
        self.assertCleaned(plain_video(references=box(b"free", MARKER)))
        h263 = self.assertCleaned(plain_video(box(b"d263", b"VNDR" + bytes([0, 10, 0])), b"s263"))
        self.assertIn(box(b"d263", bytes(7)[:4] + bytes([0, 10, 0])), h263)  # the codec's maker is cleared

    def test_an_extended_language_goes_with_the_region_it_may_name(self):
        rebuilt = self.assertCleaned(inserted(plain_video(), TRAK + [b"mdia"], full(b"elng", 0, b"zh-Hans-CN\0")))
        self.assertNotIn(b"zh-Hans", rebuilt)

    def test_boxes_players_need_are_refused_unless_checked(self):
        nclx = b"nclx" + struct.pack(">HHH", 1, 1, 1)
        self.assertCleaned(plain_video(box(b"avcC", bytes(7)) + box(b"colr", nclx + b"\x80")
                                       + box(b"dvvC", DVVC[:4] + b"\x04" + bytes(19))))  # metadata compression
        # AVFoundation's MP4 exports: the fields and their chroma locations, and the alpha mode.
        self.assertCleaned(plain_video(box(b"avcC", bytes(7)) + box(b"fiel", b"\1\0") + box(b"chrm", bytes(2))))
        self.assertCleaned(plain_video(box(b"hvcC", HVCC) + box(b"almo", bytes.fromhex("00000100")), b"hvc1"))
        # FFmpeg's iPod marker; and its copy of a ProRes encoder's description, which is emptied.
        ipod = box(b"uuid", bytes.fromhex("6b6840f25f244fc5ba39a51bcf0323f3") + bytes(4))
        self.assertIn(ipod, self.assertCleaned(plain_video(box(b"avcC", bytes(7)) + ipod)))
        prores = self.assertCleaned(plain_video(box(b"glbl", b"Apple ProRes 422"), b"apcn"))
        self.assertNotIn(b"Apple ProRes", prores)
        self.assertEqual(mp4.rebuild(prores), prores)  # a clean copy cleans to itself
        self.assertCleaned(plain_video(box(b"avcC", bytes(7)) + ipod[:-1] + b"\1"))  # mp4v2's marker
        # Apple Log, as iPhones record it.
        self.assertCleaned(plain_video(box(b"hvcC", HVCC) + box(b"logs", b"com.apple.rec2020.apple-log"), b"hvc1"))
        video = (1, b"vide", visual_entry(b"avc1", box(b"avcC", bytes(7))), VIDEO, b"", b"vmhd")
        fields = bytes(6) + struct.pack(">H", 1) + bytes(8) + struct.pack(">HHHHI", 2, 16, 0, 0, 48000 << 16)

        def layout(tag, count, bitmap=0):
            entry = box(b"mp4a", fields + esds() + full(b"chan", 0, struct.pack(">III", tag, bitmap, count)
                                                          + bytes(20 * count)))
            return movie_file(tracks=[video, (2, b"soun", entry, [AUDIO], b"", b"smhd")])

        stereo = 101 << 16 | 2
        self.assertCleaned(layout(stereo, 0))
        self.assertCleaned(layout(0, 2))  # descriptions of each channel
        self.assertRefused(layout(stereo, 1))  # a description the layout does not use
        self.assertRefused(layout(stereo, 0, bitmap=3))
        refused = {
            "360-degree video": inserted(plain_video(), TRAK, box(b"uuid", mp4.SPHERICAL + b"<rdf:SphericalVideo/>")),
            "unknown color": plain_video(box(b"avcC", bytes(7)) + box(b"colr", b"prvt" + bytes(6))),
            "color reserved bits": plain_video(box(b"avcC", bytes(7)) + box(b"colr", nclx + b"\x81")),
            "Dolby Vision reserved bits": plain_video(box(b"avcC", bytes(7)) + box(b"dvvC", DVVC[:4] + b"\x01"
                                                                                   + bytes(19))),
            "two pixel aspect ratios": plain_video(box(b"avcC", bytes(7)) + box(b"pasp", bytes(8)) * 2),
            "a chroma location of no meaning": plain_video(box(b"avcC", bytes(7)) + box(b"chrm", b"\0\x40")),
            "a hero eye of no meaning": plain_video(box(b"hvcC", HVCC) + box(b"vexu", box(b"eyes", full(
                b"stri", 0, b"\3") + full(b"hero", 0, b"\3"))), b"hvc1"),
            "an alpha mode of no known meaning": plain_video(box(b"hvcC", HVCC) + box(b"almo", b"MDPV"), b"hvc1"),
            "a uuid box other than the iPod's": plain_video(box(b"avcC", bytes(7)) + box(b"uuid", bytes(16) + MARKER)),
            "an encoder's description elsewhere": plain_video(box(b"avcC", bytes(7)) + box(b"glbl", MARKER)),
            "a log encoding of no known name": plain_video(box(b"hvcC", HVCC) + box(b"logs", MARKER), b"hvc1"),
        }
        for reason, data in refused.items():
            with self.subTest(reason):
                self.assertRefused(data)


class PipelineTests(unittest.TestCase):
    def test_videos_are_cleaned_in_a_copy_next_to_them(self):
        with tempfile.TemporaryDirectory(prefix="video-") as temp:
            folder = Path(temp, "视频 %d")
            folder.mkdir()
            source = folder / "IMG_1234.MOV"
            source.write_bytes(movie_file())
            original = source.read_bytes()
            output = core.clean(str(source), SECOND_CHECK)
            self.assertEqual(output.name, "IMG_1234_clean.MOV")
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(sorted(path.name for path in folder.iterdir()), ["IMG_1234.MOV", "IMG_1234_clean.MOV"])
            self.assertEqual(output.read_bytes(), mp4.rebuild(original))
            anonymous = core.clean(str(source), naming="anonymous")
            self.assertRegex(anonymous.name, r"^video_[0-9a-f]{32}\.mov$")

    def test_a_copy_that_fails_its_checks_is_removed(self):
        with tempfile.TemporaryDirectory(prefix="video-") as folder:
            source = Path(folder, "VID_20240501_123456.mp4")
            source.write_bytes(movie_file(quicktime=False))
            with patch.object(mp4, "verify", side_effect=VerificationError("verification_failed", detail="test")), \
                    self.assertRaises(VerificationError):
                core.clean(str(source))
            with self.assertRaises(FormatError):
                source.write_bytes(movie_file(quicktime=False, fragmented=True))
                core.clean(str(source))
            self.assertEqual(os.listdir(folder), [source.name])
            source.write_bytes(movie_file(quicktime=False))
            self.assertEqual(core.clean(str(source)).name, "VID_clean.mp4")

    @unittest.skipIf(os.name == "nt", "Windows limits the whole path")
    def test_a_long_name_is_cleaned(self):
        # The copy is cleaned under a short name, whatever the original's.
        with tempfile.TemporaryDirectory(prefix="video-") as folder:
            source = Path(folder, "v" * 240 + ".mov")
            source.write_bytes(movie_file())
            self.assertEqual(core.clean(str(source)).name, "v" * 240 + "_clean.mov")

    def test_a_video_told_apart_only_past_its_first_bytes_is_cleaned_in_a_copy(self):
        with tempfile.TemporaryDirectory(prefix="video-") as folder, patch.object(core, "HEAD", 8), \
                patch.object(core, "clean_copy", wraps=core.clean_copy) as clean_copy:
            source = Path(folder, "clip.mov")
            source.write_bytes(movie_file())
            self.assertEqual(core.clean(str(source)).name, "clip_clean.mov")
            clean_copy.assert_called_once()

    def test_a_video_emptied_while_it_is_cleaned_is_left_alone(self):
        def emptied(source, destination):
            Path(source).write_bytes(b"")
            Path(destination).write_bytes(b"")

        with tempfile.TemporaryDirectory(prefix="video-") as folder, patch.object(core.shutil, "copyfile", emptied):
            source = Path(folder, "clip.mov")
            source.write_bytes(movie_file())
            with self.assertRaises(VerificationError) as caught:
                core.clean(str(source))
            self.assertEqual(caught.exception.key, "source_changed")
            self.assertEqual(os.listdir(folder), ["clip.mov"])

    @unittest.skipIf(os.name == "nt", "Windows has no owner-only permissions")
    def test_the_copy_is_the_owners_alone_until_it_gets_the_originals_permissions(self):
        with tempfile.TemporaryDirectory(prefix="video-") as folder:
            source = Path(folder, "private.mov")
            source.write_bytes(movie_file())
            source.chmod(0o640)
            seen, clean = {}, mp4.clean

            def cleaning(original, copy):
                seen.update({path.name: path.stat().st_mode & 0o777 for path in Path(folder).iterdir()})
                return clean(original, copy)

            with patch.object(mp4, "clean", cleaning):
                output = core.clean(str(source))
            unfinished = [name for name in seen if name.startswith("magicdispel-")]
            self.assertEqual([seen[name] for name in unfinished], [0o600])  # visible, and the owner's alone
            self.assertEqual(output.stat().st_mode & 0o777, 0o640)

    def test_exiftool_is_never_given_a_path_it_would_misread(self):
        with self.assertRaises(exiftool.ExifToolError):
            exiftool.read("exiftool", None, Path("line\nbreak", "clip.mov"))

    @unittest.skipUnless(SECOND_CHECK, "needs ExifTool")
    def test_exiftool_finds_nothing_private_in_the_result(self):
        with tempfile.TemporaryDirectory(prefix="video-") as folder:
            source = Path(folder, "clip.mov")
            source.write_bytes(movie_file())
            tags = exiftool.read(SECOND_CHECK, None, core.clean(str(source), SECOND_CHECK))
            text = repr(tags)
            self.assertNotRegex(text, re.escape(MARKER.decode()))
            self.assertNotIn("31.23", text)


if __name__ == "__main__":
    unittest.main()
