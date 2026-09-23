"""HEIF/AVIF box-level cleaning: extra boxes, unused bytes, metadata items, sequences."""
import struct
import unittest

from PIL import ImageCms

from magicdispel.errors import FormatError, VerificationError
from magicdispel.formats import heif
from magicdispel.privacy import bmff_boxes, heif_layout, sanitize_icc

MARKER = b"MD_HEIF_PRIVATE"
PROFILE = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
HDR_XMP = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
           b'<rdf:Description xmlns:HDRGainMap="http://ns.apple.com/HDRGainMap/1.0/" '
           b'HDRGainMap:HDRGainMapHeadroom="2.5"><d xmlns="urn:test">' + MARKER + b'</d>'
           b'</rdf:Description></rdf:RDF></x:xmpmeta>')


def box(kind, payload):
    return struct.pack(">I", len(payload) + 8) + kind + payload


def heif_file(items, before_mdat=(), after_mdat=(), gap=b"", profile=None):
    """A small HEIF built independently of the code under test. items are
    (id, type, payload, content type); item 1 is the primary image. All data
    goes in one mdat after the extra boxes; `gap` belongs to no item."""
    infos = b"".join(box(b"infe", b"\x02\0\0\0" + struct.pack(">HH", ident, 0) + kind + b"\0"
                         + (content_type + b"\0" if kind == b"mime" else b""))
                     for ident, kind, _, content_type in items)
    iinf = box(b"iinf", b"\0" * 4 + struct.pack(">H", len(items)) + infos)
    properties = [box(b"ispe", b"\0" * 4 + struct.pack(">II", 8, 8))]
    if profile:
        properties.append(box(b"colr", b"prof" + profile))
    images = [ident for ident, kind, _, _ in items if kind == b"hvc1"]
    ipma = b"\0" * 4 + struct.pack(">I", len(images)) + b"".join(
        struct.pack(">HB", ident, len(properties)) + bytes(range(1, len(properties) + 1)) for ident in images)
    iprp = box(b"iprp", box(b"ipco", b"".join(properties)) + box(b"ipma", ipma))
    described = [ident for ident, kind, _, _ in items if kind != b"hvc1"]
    iref = box(b"iref", b"\0" * 4 + b"".join(box(b"cdsc", struct.pack(">HHH", ident, 1, 1))
                                              for ident in described)) if described else b""
    ftyp = box(b"ftyp", b"heic\0\0\0\0mif1heic")

    def meta(base):
        entries, offset = b"", 0
        for ident, _, payload, _ in items:
            entries += struct.pack(">HHHII", ident, 0, 1, base + offset, len(payload))
            offset += len(payload)
        iloc = box(b"iloc", b"\0" * 4 + bytes([0x44, 0]) + struct.pack(">H", len(items)) + entries)
        return box(b"meta", b"\0" * 4 + box(b"hdlr", b"\0" * 8 + b"pict" + b"\0" * 13)
                   + box(b"pitm", b"\0" * 4 + struct.pack(">H", 1)) + iinf + iloc + iprp + iref)

    extras = b"".join(before_mdat)
    base = len(ftyp) + len(meta(0)) + len(extras) + 8
    return (ftyp + meta(base) + extras + box(b"mdat", b"".join(p for _, _, p, _ in items) + gap)
            + b"".join(after_mdat))


def top(data):
    return [kind for kind, *_ in bmff_boxes(data)]


PRIMARY = (1, b"hvc1", b"PRIMARY_PIXELS" * 4, b"")


class HeifTests(unittest.TestCase):
    def assertRebuilt(self, data):
        rebuilt = heif.rebuild(data)
        heif.verify(data, rebuilt)
        self.assertNotIn(MARKER, rebuilt)
        self.assertIn(PRIMARY[2], rebuilt)
        return rebuilt

    def test_extra_boxes_are_emptied_in_place_or_dropped_at_the_end(self):
        data = heif_file([PRIMARY], before_mdat=[box(b"uuid", bytes(16) + MARKER), box(b"free", MARKER)],
                         after_mdat=[box(b"uuid", bytes(16) + MARKER), box(b"skip", MARKER)])
        rebuilt = self.assertRebuilt(data)
        self.assertEqual(top(rebuilt), [b"ftyp", b"meta", b"free", b"free", b"mdat"])
        self.assertEqual(len(rebuilt), len(data) - 2 * 8 - 16 - 2 * len(MARKER))

    def test_unused_media_bytes_are_zeroed(self):
        rebuilt = self.assertRebuilt(heif_file([PRIMARY], gap=MARKER))
        self.assertTrue(rebuilt.endswith(bytes(len(MARKER))))

    def test_metadata_items_go_and_xmp_keeps_only_hdr_fields(self):
        items = [PRIMARY, (2, b"Exif", b"\0\0\0\x06Exif\0\0" + MARKER, b""),
                 (3, b"mime", HDR_XMP, b"application/rdf+xml"),
                 (4, b"mime", MARKER, b"application/c2pa"), (5, b"uri ", MARKER, b"")]
        rebuilt = self.assertRebuilt(heif_file(items))
        layout = heif_layout(rebuilt)
        self.assertEqual(set(layout["items"]), {1, 3})
        packet = b"".join(rebuilt[a:b] for a, b in layout["extents"][3][1])
        self.assertIn(b'HDRGainMap:HDRGainMapHeadroom="2.5"', packet)

    def test_unknown_item_types_are_refused(self):
        with self.assertRaises(FormatError) as caught:
            heif.rebuild(heif_file([PRIMARY, (2, b"abcd", MARKER, b"")]))
        self.assertEqual(caught.exception.key, "unsupported_part")

    def test_profiles_are_sanitized(self):
        data = heif_file([PRIMARY], profile=PROFILE)
        rebuilt = self.assertRebuilt(data)
        self.assertEqual(heif.item_profiles(rebuilt, heif_layout(rebuilt), 1), [sanitize_icc(PROFILE)])

    def test_verify_notices_a_tampered_result(self):
        data = heif_file([PRIMARY], before_mdat=[box(b"uuid", bytes(16) + MARKER)], gap=bytes(8))
        rebuilt = heif.rebuild(data)
        free = rebuilt.index(b"free") + 4
        pixels = rebuilt.index(PRIMARY[2])
        for position in (free, pixels, len(rebuilt) - 1):  # emptied box, image, unused gap
            tampered = bytearray(rebuilt)
            tampered[position] ^= 0x5A
            with self.subTest(position=position), self.assertRaises(VerificationError):
                heif.verify(data, bytes(tampered))


def full(kind, version, payload):
    return box(kind, bytes([version]) + b"\0\0\0" + payload)


def movie(dref_flags=1):
    """A sequence movie box with times, names, user data and metadata to clear."""
    times = struct.pack(">QQ", 3_800_000_000, 3_800_000_001)
    entry = bytes(6) + struct.pack(">H", 1) + bytes(16) + struct.pack(">HH", 8, 8) + bytes(14) \
        + bytes([10]) + b"MD_ENCODER" + bytes(21) + bytes(4)
    stbl = box(b"stbl", full(b"stsd", 0, struct.pack(">I", 1) + box(b"av01", entry)))
    location = box(b"url ", b"\0\0\0" + bytes([dref_flags]) + MARKER)  # flag 1: data in this file
    dinf = box(b"dinf", full(b"dref", 0, struct.pack(">I", 1) + location))
    minf = box(b"minf", dinf + stbl)
    mdia = box(b"mdia", full(b"mdhd", 1, times + bytes(12)) + full(b"hdlr", 0, bytes(4) + b"pict" + bytes(12)
                                                                    + MARKER + b"\0") + minf)
    trak = box(b"trak", full(b"tkhd", 1, times + bytes(72)) + mdia + box(b"udta", box(b"date", MARKER)))
    return box(b"moov", full(b"mvhd", 0, struct.pack(">II", 3_800_000_000, 3_800_000_001) + bytes(88))
               + trak + full(b"meta", 0, box(b"ilst", MARKER)))


class SequenceTests(unittest.TestCase):
    def test_times_names_and_user_data_are_cleared(self):
        data = bytearray(movie())
        heif.clean_movie(data, 8, len(data), "AVIF")
        self.assertNotIn(MARKER, data)
        self.assertNotIn(b"MD_ENCODER", data)
        self.assertNotIn(struct.pack(">I", 3_800_000_000), data)
        heif.check_movie(bytes(data), 8, len(data))
        tampered = bytes(data).replace(b"free", b"udta", 1)
        with self.assertRaises(VerificationError):
            heif.check_movie(tampered, 8, len(tampered))

    def test_media_stored_elsewhere_is_refused(self):
        data = bytearray(movie(dref_flags=0))
        with self.assertRaises(FormatError) as caught:
            heif.clean_movie(data, 8, len(data), "AVIF")
        self.assertEqual(caught.exception.key, "unsupported_part")


if __name__ == "__main__":
    unittest.main()
