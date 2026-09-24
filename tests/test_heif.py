"""HEIF and AVIF: items, boxes and image sequences, through rebuild and verify."""
import struct
import unittest

from PIL import ImageCms

from magicdispel import icc
from magicdispel.errors import FormatError, VerificationError
from magicdispel.formats import bmff, heif

MARKER = b"MD_HEIF_PRIVATE"
PROFILE = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
HDR_XMP = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="' + MARKER + b'"><rdf:RDF '
           b'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description '
           b'xmlns:HDRGainMap="http://ns.apple.com/HDRGainMap/1.0/" HDRGainMap:HDRGainMapHeadroom="2.5">'
           b'<d xmlns="urn:test">' + MARKER + b'</d></rdf:Description></rdf:RDF></x:xmpmeta>')
HEADROOM = b'HDRGainMap:HDRGainMapHeadroom="2.5"'
DEPTH = b"urn:mpeg:hevc:2015:auxid:2"
ALPHA = b"urn:mpeg:hevc:2015:auxid:1"
GAIN_MAP = b"urn:com:apple:photo:2020:aux:hdrgainmap"
PRIMARY = (1, b"hvc1", b"PRIMARY_PIXELS")
DEPTH_IMAGE = (2, b"hvc1", b"DEPTH_PIXELS")


def box(kind, payload):
    return struct.pack(">I", len(payload) + 8) + kind + payload


def full(kind, version, payload):
    return box(kind, bytes([version, 0, 0, 0]) + payload)


def heif_file(items, refs=(), auxiliary=None, names=None, properties=(), associations=None, in_idat=False,
              overlap=False, group=(), meta_boxes=(), before_mdat=(), after_mdat=(), gap=b""):
    """A small HEIF file, built independently of the code under test.

    items are (id, type, payload), or (id, "mime", payload, content type) for
    MIME items other than XMP; item 1 is the primary image. Image items get an
    ispe property and auxiliary images ({id: type URN}) an auxC one; extra
    `properties` are left unused, and `associations` ({id: indices}) replaces
    what an item gets. Item data goes in idat, or in an mdat after the
    `before_mdat` boxes, followed by `gap`; with `overlap`, item 2's data
    starts where item 1's does. The handler name is MARKER.
    """
    auxiliary, names = auxiliary or {}, names or {}
    props = [box(b"ispe", bytes(4) + struct.pack(">II", 8, 8))]
    assigned = {ident: [1] for ident, kind, *_ in items if kind not in (b"Exif", b"mime", b"uri ")}
    for ident, urn in auxiliary.items():
        props.append(box(b"auxC", bytes(4) + urn + b"\0"))
        assigned.setdefault(ident, []).append(len(props))
    props += properties
    assigned.update(associations or {})
    ipma = bytes(4) + struct.pack(">I", len(assigned)) + b"".join(
        struct.pack(">HB", ident, len(indices)) + bytes(indices) for ident, indices in assigned.items())
    infos = b"".join(box(b"infe", b"\x02\0\0\0" + struct.pack(">HH", ident, 0) + kind + names.get(ident, b"") + b"\0"
                         + {b"mime": (rest[0] if rest else b"application/rdf+xml") + b"\0\0",
                            b"uri ": b"urn:test:private\0"}.get(kind, b""))
                     for ident, kind, _, *rest in items)
    tables = (box(b"hdlr", bytes(8) + b"pict" + bytes(12) + MARKER + b"\0")
              + full(b"pitm", 0, struct.pack(">H", 1))
              + full(b"iinf", 0, struct.pack(">H", len(items)) + infos)
              + box(b"iprp", box(b"ipco", b"".join(props)) + box(b"ipma", ipma))
              + (full(b"iref", 0, b"".join(box(kind, struct.pack(">HH%dH" % len(targets), origin, len(targets), *targets))
                                           for kind, origin, targets in refs)) if refs else b""))
    grpl = box(b"grpl", full(b"altr", 0, struct.pack(">II%dI" % len(group), 1, len(group), *group))) if group else b""
    data = b"".join(payload for _, _, payload, *_ in items) + gap
    ftyp = box(b"ftyp", b"heic\0\0\0\0mif1heic")
    extras = b"".join(before_mdat)

    def meta(base):
        entries, offset = b"", 0
        for ident, _, payload, *_ in items:
            start = base if overlap and ident == 2 else base + offset
            # Version 1 has a construction method: 1 is an offset into idat.
            entries += struct.pack(">H", ident) + (b"\0\1" if in_idat else b"") + struct.pack(">HHII", 0, 1, start,
                                                                                               len(payload))
            offset += len(payload)
        iloc = full(b"iloc", int(in_idat), bytes([0x44, 0]) + struct.pack(">H", len(items)) + entries)
        return full(b"meta", 0, tables + iloc + (box(b"idat", data) if in_idat else b"") + grpl
                    + b"".join(meta_boxes))

    if in_idat:
        return ftyp + meta(0) + extras + b"".join(after_mdat)
    base = len(ftyp) + len(meta(0)) + len(extras) + 8
    return ftyp + meta(base) + extras + box(b"mdat", data) + b"".join(after_mdat)


def top(data):
    return [found.kind for found in bmff.boxes(data)]


class HeifTests(unittest.TestCase):
    def assertRebuilt(self, data):
        rebuilt = heif.rebuild(data)
        heif.verify(data, rebuilt)
        self.assertNotIn(MARKER, rebuilt)
        self.assertIn(PRIMARY[2], rebuilt)
        return rebuilt

    def assertRefused(self, data, key):
        with self.assertRaises(FormatError) as caught:
            heif.rebuild(data)
        self.assertEqual(caught.exception.key, key)


class BoxTests(HeifTests):
    def test_extra_boxes_are_emptied_in_place_or_dropped_at_the_end(self):
        data = heif_file([PRIMARY], before_mdat=[box(b"uuid", bytes(16) + MARKER), box(b"free", MARKER)],
                         after_mdat=[box(b"uuid", bytes(16) + MARKER), box(b"skip", MARKER)])
        rebuilt = self.assertRebuilt(data)
        self.assertEqual(top(rebuilt), [b"ftyp", b"meta", b"free", b"free", b"mdat"])
        self.assertEqual(len(rebuilt), len(data) - 2 * 8 - 16 - 2 * len(MARKER))

    def test_boxes_in_meta_other_than_the_item_tables_are_emptied(self):
        rebuilt = self.assertRebuilt(heif_file([PRIMARY], meta_boxes=[full(b"xml ", 0, MARKER)]))
        self.assertEqual({child.kind for child in bmff.layout(rebuilt).children} - heif.META_KEPT, {b"free"})

    def test_unused_media_bytes_are_zeroed(self):
        for in_idat in (False, True):
            with self.subTest(in_idat=in_idat):
                self.assertRebuilt(heif_file([PRIMARY], in_idat=in_idat, gap=MARKER))

    def test_verify_notices_a_tampered_result(self):
        data = heif_file([PRIMARY], before_mdat=[box(b"uuid", bytes(16) + MARKER)], gap=bytes(8))
        rebuilt = heif.rebuild(data)
        for position in (rebuilt.index(b"free") + 4, rebuilt.index(PRIMARY[2]), len(rebuilt) - 1):
            tampered = bytearray(rebuilt)  # an emptied box, the image, an unused gap
            tampered[position] ^= 0x5A
            with self.subTest(position=position), self.assertRaises(VerificationError):
                heif.verify(data, bytes(tampered))


class ItemTests(HeifTests):
    def test_metadata_items_go_and_xmp_keeps_only_hdr_fields(self):
        items = [PRIMARY, (2, b"Exif", b"\0\0\0\x06Exif\0\0" + MARKER), (3, b"mime", HDR_XMP),
                 (4, b"mime", MARKER, b"application/c2pa"), (5, b"uri ", MARKER)]
        rebuilt = self.assertRebuilt(heif_file(items, refs=[(b"cdsc", ident, [1]) for ident in (2, 3, 4, 5)]))
        layout = bmff.layout(rebuilt)
        self.assertEqual(set(layout.items), {1, 3})
        self.assertIn(HEADROOM, b"".join(rebuilt[a:b] for a, b in layout.extents[3]))

    def test_metadata_items_emptied_by_other_programs_are_removed(self):
        # ExifTool's -all= leaves the EXIF and XMP items in place with no data.
        items = [PRIMARY, (2, b"Exif", b""), (3, b"mime", b""), (4, b"uri ", MARKER)]
        rebuilt = self.assertRebuilt(heif_file(items, refs=[(b"cdsc", ident, [1]) for ident in (2, 3, 4)]))
        self.assertEqual(set(bmff.layout(rebuilt).items), {1})

    def test_editing_images_and_thumbnails_go_and_display_images_stay(self):
        # Item 3 is unreadable, but describes only the depth image and goes with
        # it. XMP item 7 has no HDR fields and goes; item 8 keeps its headroom.
        items = [PRIMARY, DEPTH_IMAGE, (3, b"mime", b"<CALIBRATION"), (4, b"hvc1", b"THUMBNAIL_PIXELS"),
                 (5, b"hvc1", b"GAIN_MAP_PIXELS"), (6, b"hvc1", b"ALPHA_PIXELS"),
                 (7, b"mime", b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><d xmlns="urn:test">1</d></x:xmpmeta>'),
                 (8, b"mime", HDR_XMP)]
        refs = [(b"auxl", 2, [1]), (b"cdsc", 3, [2]), (b"thmb", 4, [1]), (b"auxl", 5, [1]), (b"auxl", 6, [1]),
                (b"cdsc", 7, [5]), (b"cdsc", 8, [5])]
        for in_idat in (False, True):
            with self.subTest(in_idat=in_idat):
                data = heif_file(items, refs, {2: DEPTH, 5: GAIN_MAP, 6: ALPHA}, names={1: MARKER},
                                 properties=[box(b"abcd", MARKER)], in_idat=in_idat)
                rebuilt = self.assertRebuilt(data)
                self.assertEqual(len(rebuilt), len(data))
                self.assertEqual(heif.rebuild(rebuilt), rebuilt)
                layout = bmff.layout(rebuilt)
                self.assertEqual(set(layout.items), {1, 5, 6, 8})
                self.assertEqual(layout.idat, bmff.layout(data).idat)
                for gone in (b"DEPTH_PIXELS", b"CALIBRATION", b"THUMBNAIL_PIXELS", b"urn:test"):
                    self.assertNotIn(gone, rebuilt)
                for kept in (b"GAIN_MAP_PIXELS", b"ALPHA_PIXELS", HEADROOM):
                    self.assertIn(kept, rebuilt)

    def test_tiles_go_with_the_images_that_alone_use_them(self):
        items = [(1, b"grid", b"PRIMARY_PIXELS"), (2, b"grid", b"DEPTH_GRID"), (3, b"hvc1", b"SHARED_TILE"),
                 (4, b"hvc1", b"DEPTH_TILE"), (5, b"mime", HDR_XMP)]
        refs = [(b"dimg", 1, [3]), (b"dimg", 2, [3, 4]), (b"auxl", 2, [1]), (b"cdsc", 5, [1, 2])]
        rebuilt = self.assertRebuilt(heif_file(items, refs, {2: DEPTH}))
        layout = bmff.layout(rebuilt)
        self.assertEqual(set(layout.items), {1, 3, 5})
        self.assertIn((b"cdsc", 5, [1]), layout.references)
        self.assertNotIn(b"DEPTH_TILE", rebuilt)
        self.assertIn(b"SHARED_TILE", rebuilt)

    def test_layouts_that_cannot_be_cleaned_safely_are_refused(self):
        items, depth = [PRIMARY, DEPTH_IMAGE], {2: DEPTH}
        cases = [
            ("unsupported_part", heif_file(items, [(b"dimg", 1, [2])], depth)),         # displayed image needs depth
            ("unsupported_part", heif_file(items, [(b"auxl", 2, [1])], depth, overlap=True)),
            ("unsupported_part", heif_file(items, [(b"auxl", 2, [1])], depth, group=[1, 2])),
            ("unsupported_part", heif_file(items, [(b"auxl", 2, [1])], {2: b"urn:test:unknown"})),
            ("unsupported_part", heif_file(items, [(b"abcd", 1, [2])], depth)),
            ("unsupported_part", heif_file([PRIMARY, (2, b"abcd", MARKER)])),
            ("unsupported_part", heif_file([PRIMARY, (2, b"Exif", MARKER)], [(b"dimg", 1, [2])])),
            ("damaged", heif_file(items, [(b"auxl", 2, [99])], depth)),
            ("damaged", heif_file(items, [(b"auxl", 2, [1])], depth, associations={1: [99]})),
        ]
        for index, (key, data) in enumerate(cases):
            with self.subTest(case=index):
                self.assertRefused(data, key)

    def test_only_display_properties_stay(self):
        # A description and a creation time go; a property of unknown meaning goes
        # too, unless a reader may not ignore it (essential): then the file is refused.
        description = full(b"udes", 0, b"en\0" + MARKER + b"\0\0\0")
        created = full(b"crtt", 1, struct.pack(">Q", 3_900_000_000_000))
        data = heif_file([PRIMARY], properties=[description, created, box(b"abcd", MARKER)],
                         associations={1: [1, 2, 3, 4]})
        layout = bmff.layout(self.assertRebuilt(data))
        self.assertEqual([layout.props[index].kind if index else 0 for index in layout.associations[1]],
                         [b"ispe", 0, 0, 0])
        self.assertRefused(heif_file([PRIMARY], properties=[box(b"abcd", MARKER)], associations={1: [1, 2 | 0x80]}),
                           "unsupported_part")

    def test_fixed_size_properties_may_not_carry_extra_bytes(self):
        self.assertRefused(heif_file([PRIMARY], properties=[box(b"irot", b"\0" + MARKER)], associations={1: [1, 2]}),
                           "unsupported_part")
        # Nor may a decoder configuration, after its end.
        hvcc = bytes(22) + b"\0"  # the fields, and no arrays of parameter sets
        self.assertRebuilt(heif_file([PRIMARY], properties=[box(b"hvcC", hvcc)], associations={1: [1, 2]}))
        self.assertRefused(heif_file([PRIMARY], properties=[box(b"hvcC", hvcc + MARKER)], associations={1: [1, 2]}),
                           "unsupported_part")

    def test_profiles_are_sanitized(self):
        data = heif_file([PRIMARY], properties=[box(b"colr", b"prof" + PROFILE)], associations={1: [1, 2]})
        rebuilt = self.assertRebuilt(data)
        self.assertEqual(heif.item_profiles(rebuilt, bmff.layout(rebuilt), 1), [icc.sanitize(PROFILE)])


def sequence(dref_flags=1, handler=b"pict", movie_box=b"", track_box=b"", entry_box=b""):
    """An AVIF sequence of one sample, with times, names, user data and
    metadata to clear. dref flag 1 means the media is in this file. The extra
    boxes go into the movie, the track and the sample entry."""
    times = struct.pack(">QQ", 3_800_000_000, 3_800_000_001)
    entry = bytes(6) + struct.pack(">H", 1) + bytes(16) + struct.pack(">HH", 8, 8) + bytes(14) \
        + bytes([10]) + b"MD_ENCODER" + bytes(21) + bytes(4)
    ftyp = box(b"ftyp", b"avis\0\0\0\0avisavifmsf1")

    def movie(offset):
        stbl = box(b"stbl", full(b"stsd", 0, struct.pack(">I", 1) + box(b"av01", entry + entry_box))
                   + full(b"stts", 0, struct.pack(">III", 1, 1, 1)) + full(b"stsc", 0, struct.pack(">IIII", 1, 1, 1, 1)) + full(b"stsz", 0, struct.pack(">II", 16, 1))
                   + full(b"stco", 0, struct.pack(">II", 1, offset)))
        location = box(b"url ", b"\0\0\0" + bytes([dref_flags]) + MARKER)
        dinf = box(b"dinf", full(b"dref", 0, struct.pack(">I", 1) + location))
        mdia = box(b"mdia", full(b"mdhd", 1, times + bytes(16))
                   + full(b"hdlr", 0, bytes(4) + handler + bytes(12) + MARKER + b"\0") + box(b"minf", dinf + stbl))
        trak = box(b"trak", full(b"tkhd", 1, times + bytes(76)) + mdia + box(b"udta", box(b"date", MARKER))
                   + track_box)
        return box(b"moov", full(b"mvhd", 0, struct.pack(">II", 3_800_000_000, 3_800_000_001) + bytes(88))
                   + movie_box + trak + full(b"meta", 0, box(b"ilst", MARKER)))

    offset = len(ftyp) + len(movie(0)) + 8
    return ftyp + movie(offset) + box(b"mdat", b"SAMPLE_PIXELS..." + MARKER)


class SequenceTests(unittest.TestCase):
    def test_times_names_and_user_data_are_cleared(self):
        data = sequence()
        rebuilt = heif.rebuild(data)
        heif.verify(data, rebuilt)
        for gone in (MARKER, b"MD_ENCODER", struct.pack(">I", 3_800_000_000)):
            self.assertNotIn(gone, rebuilt)
        self.assertIn(b"SAMPLE_PIXELS...", rebuilt)
        with self.assertRaises(VerificationError):
            heif.verify(data, rebuilt.replace(b"free", b"udta", 1))

    def test_only_boxes_that_play_the_sequence_stay(self):
        # Private boxes in the movie, the track and the sample entry, of types
        # no list of metadata boxes could name in advance.
        data = sequence(movie_box=box(b"prvt", MARKER), track_box=box(b"xtra", MARKER),
                        entry_box=box(b"av1C", b"\x81\0\0\0") + box(b"prvt", MARKER))
        rebuilt = heif.rebuild(data)
        heif.verify(data, rebuilt)
        self.assertNotIn(MARKER, rebuilt)
        self.assertIn(b"SAMPLE_PIXELS...", rebuilt)
        self.assertIn(box(b"av1C", b"\x81\0\0\0"), rebuilt)
        for kind in (b"prvt", b"xtra"):
            with self.subTest(kind=kind), self.assertRaises(VerificationError):
                heif.verify(data, rebuilt.replace(b"free", kind, 1))

    def test_tracks_other_than_pictures_are_refused(self):
        for handler in (b"meta", b"soun"):
            with self.subTest(handler=handler), self.assertRaises(FormatError) as caught:
                heif.rebuild(sequence(handler=handler))
            self.assertEqual(caught.exception.key, "unsupported_part")

    def test_nothing_rides_along_in_what_a_sequence_keeps(self):
        planted = {
            "a reference that is no reference": sequence(track_box=box(b"tref", box(b"free", MARKER))),
            "fragment defaults": sequence(movie_box=box(b"mvex", full(b"trex", 0, bytes(4) + MARKER))),
            "an unchecked color volume": sequence(entry_box=box(b"cclv", MARKER)),
            "a second file type box": sequence() + box(b"ftyp", b"avis\0\0\0\0" + MARKER),
        }
        for reason, data in planted.items():
            with self.subTest(reason):
                rebuilt = heif.rebuild(data)
                heif.verify(data, rebuilt)
                self.assertNotIn(MARKER, rebuilt)
        # A compatible brand that says nothing of how to read the file is cleared.
        rebuilt = heif.rebuild(sequence().replace(b"avisavifmsf1", b"avisavifMDPV"))
        self.assertTrue(rebuilt.startswith(box(b"ftyp", b"avis\0\0\0\0avisavif" + bytes(4))))
        for reason, entry_box in {"coding constraints": full(b"ccst", 0, bytes(4) + MARKER),
                                  "an auxiliary image other than alpha": full(b"auxi", 0, b"urn:depth\0")}.items():
            with self.subTest(reason), self.assertRaises(FormatError):
                heif.rebuild(sequence(entry_box=entry_box))
        alpha = (full(b"auxi", 0, b"urn:mpeg:mpegB:cicp:systems:auxiliary:alpha\0")
                 + full(b"ccst", 0, b"\x7c" + bytes(3)))
        self.assertIn(alpha, heif.rebuild(sequence(entry_box=alpha)))
        apple = box(b"auxi", b"urn:mpeg:hevc:2015:auxid:1\0")  # as ImageIO writes it, in no full box
        self.assertIn(apple, heif.rebuild(sequence(entry_box=apple)))
        # A still image of another codec, as its brand says.
        avci = sequence().replace(b"avis\0\0\0\0avisavifmsf1", b"avci\0\0\0\0avisavifmsf1")
        self.assertTrue(heif.rebuild(avci).startswith(avci[:24]))

    def test_sharing_is_found_whatever_empty_extents_there_are(self):
        from magicdispel.formats import movie
        spans = movie.merged([(0, 10), (20, 20), (30, 40)])
        self.assertEqual(spans, [(0, 10), (30, 40)])
        self.assertTrue(movie.overlaps(movie.merged([(0, 10), (20, 20)]), 5, 30))
        self.assertFalse(movie.overlaps(spans, 10, 30))

    def test_a_thumbnail_track_goes_as_thumbnail_images_do(self):
        # A second track of the same pictures, a thumbnail of the first, which
        # goes into the movie box before mdat: every chunk offset grows by its size.
        base = sequence()
        moov = next(found for found in bmff.boxes(base) if found.kind == b"moov")
        trak = next(found for found in bmff.boxes(base, moov.content, moov.end) if found.kind == b"trak")
        second = bytearray(box(b"tref", box(b"thmb", struct.pack(">I", 0))) + base[trak.content:trak.end])
        second = bytearray(box(b"trak", bytes(second)))
        tkhd = second.index(b"tkhd") + 4
        second[tkhd + 20:tkhd + 24] = struct.pack(">I", 2)  # after version 1's times
        stco = second.index(b"stco") + 12
        offset = int.from_bytes(second[stco:stco + 4], "big") + len(second)
        second[stco:stco + 4] = struct.pack(">I", offset)
        data = sequence(movie_box=bytes(second))
        rebuilt = heif.rebuild(data)
        heif.verify(data, rebuilt)
        moov = next(found for found in bmff.boxes(rebuilt) if found.kind == b"moov")
        self.assertEqual([found.kind for found in bmff.boxes(rebuilt, moov.content, moov.end)].count(b"trak"), 1)
        self.assertIn(b"SAMPLE_PIXELS...", rebuilt)

    def test_media_stored_elsewhere_is_refused(self):
        with self.assertRaises(FormatError) as caught:
            heif.rebuild(sequence(dref_flags=0))
        self.assertEqual(caught.exception.key, "unsupported_part")


class ToneMapTests(HeifTests):
    """ISO 21496-1 gain-map metadata in a tone-mapped image (tmap) item."""
    METADATA = struct.pack(">HHB", 0, 0, 0x40) + b"".join(struct.pack(">II", n, 1_000_000) for n in range(7))

    def tone_mapped(self, payload):
        return heif_file([PRIMARY, (2, b"tmap", payload), (3, b"hvc1", b"GAIN_MAP_PIXELS")],
                         refs=[(b"dimg", 2, [1, 3])], group=(2, 1))

    def test_metadata_in_its_exact_layout_is_kept(self):
        reserved = self.METADATA[:4] + bytes([self.METADATA[4] | 0x33]) + self.METADATA[5:]
        newer = struct.pack(">HH", 0, 1) + self.METADATA[4:]
        for metadata in (self.METADATA, reserved, newer):
            with self.subTest(metadata=metadata[:5]):
                rebuilt = self.assertRebuilt(self.tone_mapped(b"\0" + metadata))
                self.assertIn(b"\0" + metadata, rebuilt)

    def test_anything_else_is_refused(self):
        # An item cannot be shortened in place, so anything after the fields is refused.
        unreadable = struct.pack(">HH", 1, 0) + self.METADATA[4:]
        for payload in (b"\0" + self.METADATA + MARKER, b"\0" + self.METADATA[:-4], b"\1" + self.METADATA,
                        b"\0" + unreadable, b"\0" + self.METADATA[:5] + bytes(56)):
            with self.subTest(size=len(payload)):
                self.assertRefused(self.tone_mapped(payload), "unsupported_part")


if __name__ == "__main__":
    unittest.main()
