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
                    b"gmhd": box(b"gmhd", MARKER)}[header]
    minf = box(b"minf", media_header + handler(b"alis", b"Core Media Data Handler " + MARKER, b"dhlr")
               + box(b"dinf", full(b"dref", 0, struct.pack(">I", 1) + full(b"alis", 0, b"", flags=1)))
               + box(b"stbl", full(b"stsd", 0, struct.pack(">I", 1) + entry) + table(offset, sizes)))
    mdia = box(b"mdia", full(b"mdhd", 0, TIMES + struct.pack(">IIHH", 600, 1200, 0x55C4, 0))
               + handler(kind, b"Core Media " + MARKER) + minf)
    tkhd = full(b"tkhd", 0, TIMES + struct.pack(">III", ident, 0, 1200) + bytes(16) + struct.pack(
        ">9I", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000) + struct.pack(">II", 64 << 16, 48 << 16), flags=7)
    return box(b"trak", tkhd + (box(b"tref", references) if references else b"")
               + box(b"edts", full(b"elst", 0, struct.pack(">IIII", 1, 1200, 0, 0x10000))) + mdia + extra)


def movie_file(quicktime=True, typed=True, tracks=None, movie_extra=b"", top_extra=b"", fragmented=False):
    """A small video, QuickTime-style (moov after mdat) or MP4, with a video
    and a sound track, and metadata to remove: an Apple-style metadata track,
    a timecode track, a chapter track, user data, QuickTime metadata keys, a
    uuid box before mdat and Samsung-style SEF data and junk after moov.
    `tracks` replaces the default tracks: [(id, handler, sample entry,
    [samples], references, media header)]."""
    ftyp = box(b"ftyp", b"qt  \0\0\0\0qt  ") if quicktime else box(b"ftyp", b"mp42\0\0\0\0isommp42")
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


def boxes_at(data, path):
    """The boxes at a path of box types, such as [b"moov", b"trak"]."""
    found = [bmff.Box(b"", 0, 0, len(data))]
    for kind in path:
        found = [child for parent in found for child in bmff.boxes(data, parent.content, parent.end)
                 if child.kind == kind]
    return found


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
