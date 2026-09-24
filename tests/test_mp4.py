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


def sound_entry(quicktime):
    if quicktime:  # version 1, with QuickTime's sound extension
        fields = (bytes(6) + struct.pack(">HHH", 1, 1, 0) + b"appl" + struct.pack(">HHhHI", 2, 16, -2, 0, 48000 << 16)
                  + struct.pack(">IIII", 1024, 0, 4, 2))
        wave = box(b"wave", box(b"frma", b"mp4a") + box(b"mp4a", bytes(4)) + full(b"esds", 0, bytes(20))
                   + bytes(8))
        return box(b"mp4a", fields + wave)
    fields = bytes(6) + struct.pack(">H", 1) + bytes(8) + struct.pack(">HHHHI", 2, 16, 0, 0, 48000 << 16)
    return box(b"mp4a", fields + full(b"esds", 0, bytes(20)))


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


def movie_file(quicktime=True, typed=True, tracks=None, movie_extra=b"", top_extra=b"", fragmented=False, ftyp=None):
    """A small video, QuickTime-style or MP4, with a video and a sound track,
    and metadata to remove: an Apple-style metadata track, a timecode track, a
    chapter track, user data, QuickTime metadata keys, a uuid box before mdat
    and Samsung-style SEF data and junk after moov. The movie box comes after
    the media, so it can be edited without moving any sample (see edited).
    `tracks` replaces the default tracks: [(id, handler, sample entry,
    [samples], references, media header)]."""
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
               + traks + box(b"udta", box(b"\xa9xyz", MARKER)) + box(b"meta", handler(b"mdta", b"") + keys + ilst)
               + movie_extra)
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


class MovieTests(unittest.TestCase):
    def assertCleaned(self, data):
        rebuilt = mp4.rebuild(data)
        mp4.verify(data, rebuilt)
        self.assertNotIn(MARKER, rebuilt)
        return rebuilt

    def assertRefused(self, data, key="unsupported_part"):
        with self.assertRaises(FormatError) as caught:
            mp4.rebuild(data)
        self.assertEqual(caught.exception.key, key)

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


class TableTests(MovieTests):
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
        }
        for reason, broken in damaged.items():
            with self.subTest(reason):
                self.assertRefused(broken, "damaged")

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
        def groups(kind, description, size, samples=2, index=1, version=1):
            return (full(b"sgpd", version, kind + struct.pack(">II", size, 1) + description)
                    + full(b"sbgp", 0, kind + struct.pack(">III", 1, samples, index)))

        roll = groups(b"roll", struct.pack(">h", -1), 2)
        data = inserted(plain_video(), STBL, roll + groups(b"prvt", MARKER, 16))
        rebuilt = self.assertCleaned(data)
        self.assertIn(roll, rebuilt)
        self.assertNotIn(b"prvt", rebuilt)
        refused = {
            "another size": groups(b"roll", bytes(3), 3),
            "past the descriptions": groups(b"roll", bytes(2), 2, index=2),
            "past the samples": groups(b"roll", bytes(2), 2, samples=3),
            "reserved bits": groups(b"sync", b"\xc1", 1),
        }
        for reason, boxes in refused.items():
            with self.subTest(reason):
                self.assertRefused(inserted(plain_video(), STBL, boxes))
        self.assertRefused(inserted(plain_video(), STBL, roll + roll), "damaged")

    def test_quicktime_sound_is_read_by_its_sample_entry(self):
        # Uncompressed sound whose table counts frames of one time unit, each of
        # "size 1": players read each chunk as frames times their size in the entry.
        sowt = box(b"sowt", bytes(6) + struct.pack(">HHH", 1, 0, 0) + b"appl"
                   + struct.pack(">HHhHI", 2, 16, 0, 0, 48000 << 16))
        video = (1, b"vide", visual_entry(b"avc1", box(b"avcC", bytes(7))), VIDEO, b"", b"vmhd")
        frames = [b"PCM_", b"FRAM", b"ES_B", b"YTES"]

        def one_sized(entry):
            data = movie_file(tracks=[video, (2, b"soun", entry, frames, b"", b"smhd")])
            data = replaced(data, full(b"stts", 0, struct.pack(">III", 1, 4, 512)),
                            full(b"stts", 0, struct.pack(">III", 1, 4, 1)))
            return replaced(data, full(b"stsz", 0, struct.pack(">6I", 0, 4, 4, 4, 4, 4)),
                            full(b"stsz", 0, struct.pack(">II", 1, 4)))

        data = one_sized(sowt)
        self.assertEqual(self.assertCleaned(data).find(b"PCM_FRAMES_BYTES"), data.find(b"PCM_FRAMES_BYTES"))
        self.assertRefused(one_sized(sound_entry(False)))  # compressed, with no sizes to read chunks by

    def test_the_file_type_box_holds_only_known_brands(self):
        self.assertRefused(movie_file(quicktime=False).replace(b"isommp42", b"isomMDPV"))
        padded = box(b"ftyp", b"qt  \x20\x05\x03\x00qt  " + bytes(8))  # as older QuickTime writes it
        self.assertTrue(self.assertCleaned(movie_file(ftyp=padded)).startswith(padded))

    def test_what_players_do_not_need_goes(self):
        rebuilt = self.assertCleaned(inserted(plain_video(), [b"moov"], box(b"mvex", full(b"trex", 0, bytes(20)))))
        self.assertNotIn(b"trex", rebuilt)
        self.assertCleaned(plain_video(references=box(b"free", MARKER)))
        h263 = self.assertCleaned(plain_video(box(b"d263", b"VNDR" + bytes([0, 10, 0])), b"s263"))
        self.assertIn(box(b"d263", bytes(7)[:4] + bytes([0, 10, 0])), h263)  # the codec's maker is cleared

    def test_the_language_stays_when_it_is_one(self):
        mdia = TRAK + [b"mdia"]
        language = full(b"elng", 0, b"zh-Hant\0")
        self.assertIn(language, self.assertCleaned(inserted(plain_video(), mdia, language)))
        for text in (b"zh Hant\0", b"zh-Hant", b"\0"):
            with self.subTest(text):
                self.assertRefused(inserted(plain_video(), mdia, full(b"elng", 0, text)))

    def test_boxes_players_need_are_refused_unless_checked(self):
        nclx = b"nclx" + struct.pack(">HHH", 1, 1, 1)
        self.assertCleaned(plain_video(box(b"avcC", bytes(7)) + box(b"colr", nclx + b"\x80")
                                       + box(b"dvvC", DVVC[:4] + b"\x04" + bytes(19))))  # metadata compression
        refused = {
            "360-degree video": inserted(plain_video(), TRAK, box(b"uuid", mp4.SPHERICAL + b"<rdf:SphericalVideo/>")),
            "unknown color": plain_video(box(b"avcC", bytes(7)) + box(b"colr", b"prvt" + bytes(6))),
            "color reserved bits": plain_video(box(b"avcC", bytes(7)) + box(b"colr", nclx + b"\x81")),
            "Dolby Vision reserved bits": plain_video(box(b"avcC", bytes(7)) + box(b"dvvC", DVVC[:4] + b"\x01"
                                                                                   + bytes(19))),
            "two pixel aspect ratios": plain_video(box(b"avcC", bytes(7)) + box(b"pasp", bytes(8)) * 2),
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
