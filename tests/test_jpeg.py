"""JPEG rebuilding: what is kept, dropped and refused, including MPF files."""
import io
import struct
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from magicdispel import core, exif
from magicdispel.errors import FormatError, VerificationError
from magicdispel.formats import jpeg

MARKER = b"MD_JPEG_PRIVATE"
HDR_XMP = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
           b'<rdf:Description xmlns:hdrgm="http://ns.adobe.com/hdr-gain-map/1.0/" '
           b'xmlns:dc="http://purl.org/dc/elements/1.1/" hdrgm:Version="1.0" hdrgm:GainMapMax="2.5">'
           b'<dc:creator>' + MARKER + b'</dc:creator></rdf:Description></rdf:RDF></x:xmpmeta>')


def encode(image, **options):
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=90, **options)
    return buffer.getvalue()


def gradient(mode="RGB", size=(24, 16)):
    image = Image.new("RGB", size)
    image.putdata([(x * 10, y * 15, (x * y) % 256) for y in range(size[1]) for x in range(size[0])])
    return image.convert(mode)


def app(marker, payload):
    return bytes((0xFF, marker)) + struct.pack(">H", len(payload) + 2) + payload


def with_segments(data, *segments):
    """Insert whole segments right after SOI."""
    return data[:2] + b"".join(segments) + data[2:]


def markers(data):
    return [(marker, payload[:5]) for marker, _, _, payload in jpeg.segments(data)]


def apple_note(private=MARKER):
    """An Apple maker note with HDR headroom/gain and one private field."""
    entries = [(0x0021, 10, 1, struct.pack(">ii", 3, 2)), (0x0030, 10, 1, struct.pack(">ii", 1, 4)),
               (0x000B, 2, len(private), private)]
    values_start = 16 + 12 * len(entries) + 4
    table, values = b"", b""
    for tag, kind, count, value in entries:
        table += struct.pack(">HHII", tag, kind, count, values_start + len(values))
        values += value
    return b"Apple iOS\0\0\1MM" + struct.pack(">H", len(entries)) + table + b"\0" * 4 + values


def exif_block(maker_note=None, thumbnail=None):
    """EXIF (as Pillow writes it) with display fields, private fields, and
    optionally an Apple maker note and an IFD1 thumbnail."""
    tags = Image.Exif()
    tags[0x0112], tags[0x011A], tags[0x011B], tags[0x0128] = 6, 300.0, 300.0, 2
    tags[0x010F], tags[0x0131] = MARKER.decode(), "Editor " + MARKER.decode()
    exif_ifd = tags.get_ifd(0x8769)
    exif_ifd[0xA001], exif_ifd[0x9003] = 1, "2026:09:23 17:40:00"
    if maker_note:
        exif_ifd[0x927C] = maker_note
    data = tags.tobytes()
    if thumbnail:
        # Point IFD0's next-IFD link at an IFD1 whose thumbnail carries MARKER.
        tiff = bytearray(data[6:] if data.startswith(b"Exif\0\0") else data)
        order = "<" if tiff[:2] == b"II" else ">"
        count = struct.unpack_from(order + "H", tiff, 8)[0]
        ifd1 = len(tiff)
        struct.pack_into(order + "I", tiff, 8 + 2 + 12 * count, ifd1)
        tiff += (struct.pack(order + "H", 2) + struct.pack(order + "HHII", 0x0201, 4, 1, ifd1 + 30)
                 + struct.pack(order + "HHII", 0x0202, 4, 1, len(thumbnail)) + b"\0" * 4 + thumbnail)
        data = bytes(tiff)
    return data if data.startswith(b"Exif\0\0") else b"Exif\0\0" + data


def iso_metadata(channels=1, common=False, tail=b""):
    """ISO 21496-1 gain-map metadata, laid out independently of the code under
    test: versions, flags, then the headrooms and per-channel fractions."""
    flags = (0x80 if channels == 3 else 0) | 0x40 | (0x08 if common else 0)
    fractions = 2 + 5 * channels
    if common:
        body = struct.pack(">I", 1_000_000) + struct.pack(">%dI" % fractions, *range(fractions))
    else:
        body = b"".join(struct.pack(">II", value, 1_000_000) for value in range(fractions))
    return struct.pack(">HHB", 0, 0, flags) + body + tail


def mpf(frames, extra_tags=(), entries=None):
    """An MPF file built independently of the code under test, with the index
    right after SOI; extra_tags are (tag, bytes) pairs stored after the list.
    entries are (flags, dependent1, dependent2) for each image; by default a
    primary image and images of undefined type."""
    entries = entries or [(0x030000, 0, 0)] + [(0, 0, 0)] * (len(frames) - 1)
    count = 3 + len(extra_tags)
    table_offset = 8 + 2 + 12 * count + 4
    extra_offset = table_offset + 16 * len(frames)
    directory = (struct.pack(">HHI4s", 0xB000, 7, 4, b"0100") + struct.pack(">HHII", 0xB001, 4, 1, len(frames))
                 + struct.pack(">HHII", 0xB002, 7, 16 * len(frames), table_offset))
    blobs = b""
    for tag, value in extra_tags:
        directory += struct.pack(">HHII", tag, 7, len(value), extra_offset + len(blobs))
        blobs += value
    header = b"MPF\0MM\0*" + struct.pack(">IH", 8, count) + directory + b"\0" * 4
    segment_size = 4 + len(header) + 16 * len(frames) + len(blobs)
    lengths = [len(frames[0]) + segment_size] + [len(frame) for frame in frames[1:]]
    table, position = b"", 0
    for index, ((flags, dependent1, dependent2), length) in enumerate(zip(entries, lengths)):
        offset = 0 if index == 0 else position - 10  # relative to the index's TIFF header
        table += struct.pack(">IIIHH", flags, length, offset, dependent1, dependent2)
        position += length
    index = b"\xff\xe2" + struct.pack(">H", segment_size - 2) + header + table + blobs
    return frames[0][:2] + index + frames[0][2:] + b"".join(frames[1:])


def image_list(data):
    """The byte order of an MPF index and where its image list starts: 16
    bytes per image (flags, size, offset from the index's TIFF header, and
    two dependent images)."""
    header = data.index(b"MPF\0") + 4
    order = "<" if data[header:header + 2] == b"II" else ">"
    directory = header + struct.unpack_from(order + "I", data, header + 4)[0]
    for index in range(struct.unpack_from(order + "H", data, directory)[0]):
        tag, _, _, value = struct.unpack_from(order + "HHII", data, directory + 2 + 12 * index)
        if tag == 0xB002:
            return order, header, header + value
    raise AssertionError("no MPF image list")


def pillow_mpo(count):
    """A multi-picture file as Pillow writes it, with private EXIF. Pillow
    records the third image's size as a running total; that is corrected
    here from the image's offset."""
    frames = [gradient(size=(48 - 8 * n, 32 - 4 * n)) for n in range(count)]
    tags = Image.Exif()
    tags[0x013B] = tags[0x0131] = MARKER.decode()  # artist and software
    buffer = io.BytesIO()
    frames[0].save(buffer, "MPO", save_all=True, append_images=frames[1:], exif=tags)
    data = bytearray(buffer.getvalue())
    order, header, images = image_list(data)
    last = images + 16 * (count - 1)
    struct.pack_into(order + "I", data, last + 4, len(data) - header - struct.unpack_from(order + "I", data, last + 8)[0])
    return bytes(data)


def decoded(data):
    with Image.open(io.BytesIO(data)) as image:
        frames = []
        for index in range(getattr(image, "n_frames", 1)):
            image.seek(index)
            frames.append((image.mode, image.size, image.tobytes()))
        return frames


class RebuildTests(unittest.TestCase):
    def assertRebuilt(self, data):
        rebuilt = jpeg.rebuild(data)
        jpeg.verify(data, rebuilt)
        self.assertEqual(decoded(rebuilt), decoded(data))
        self.assertEqual(jpeg.coding_hash(rebuilt), jpeg.coding_hash(data))
        self.assertNotIn(MARKER, rebuilt)
        return rebuilt

    def test_private_segments_are_dropped_and_display_fields_kept(self):
        data = with_segments(encode(gradient(), dpi=(300, 300)),
                             app(0xE1, exif_block()), app(0xE1, b"http://ns.adobe.com/xap/1.0/\0" + HDR_XMP),
                             app(0xED, b"Photoshop 3.0\0" + MARKER), app(0xEB, b"JP\0\x01" + MARKER),
                             app(0xE9, b"PRIV\0" + MARKER), app(0xFE, MARKER)) + MARKER
        rebuilt = self.assertRebuilt(data)
        found = dict((marker, payload) for marker, _, _, payload in jpeg.segments(rebuilt) if marker >= 0xE0)
        self.assertEqual(sorted(found), [0xE0, 0xE1])  # JFIF, then EXIF (the HDR XMP shares APP1)
        fields = exif.display_fields(next(p for m, _, _, p in jpeg.segments(rebuilt)
                                          if m == 0xE1 and p.startswith(b"Exif")))
        self.assertEqual((fields.orientation, fields.resolution[2], fields.color_space), (6, 2, 1))
        with Image.open(io.BytesIO(rebuilt)) as image:
            self.assertEqual(tuple(round(v) for v in image.info["dpi"]), (300, 300))
        packet = next(p for m, _, _, p in jpeg.segments(rebuilt) if m == 0xE1 and p.startswith(b"http"))
        self.assertIn(b'hdrgm:GainMapMax="2.5"', packet)
        self.assertNotIn(b"creator", packet)

    def test_thumbnails_are_dropped(self):
        jfif = next(p for m, _, _, p in jpeg.segments(encode(gradient())) if m == 0xE0)
        thumbnail_jfif = jfif[:12] + b"\x02\x02" + MARKER[:12]
        data = with_segments(encode(gradient())[:2] + encode(gradient())[20:],
                             app(0xE0, thumbnail_jfif), app(0xE1, exif_block(thumbnail=MARKER * 3)))
        rebuilt = self.assertRebuilt(data)
        self.assertEqual(len(next(p for m, _, _, p in jpeg.segments(rebuilt) if m == 0xE0)), 14)

    def test_apple_hdr_values_are_kept_alone(self):
        data = with_segments(encode(gradient()), app(0xE1, exif_block(maker_note=apple_note())))
        rebuilt = self.assertRebuilt(data)
        note = exif.display_fields(next(p for m, _, _, p in jpeg.segments(rebuilt) if m == 0xE1)).apple_hdr
        self.assertEqual(note, exif.apple_hdr_note(apple_note()))
        self.assertEqual(struct.unpack_from(">H", note, 14)[0], 2)

    def test_color_and_hdr_segments_are_kept(self):
        curve = b"AROT\0\0" + struct.pack(">I", 2) + struct.pack(">2f", 0.5, 1.0) + bytes(8)
        iso = b"urn:iso:std:iso:ts:21496:-1\0" + iso_metadata()
        data = with_segments(encode(gradient("CMYK")), app(0xEA, curve), app(0xE2, iso))
        rebuilt = self.assertRebuilt(data)
        kept = [marker for marker, _ in markers(rebuilt) if marker >= 0xE0]
        self.assertEqual(kept, [0xEA, 0xE2, 0xEE])  # the Adobe segment is needed for CMYK
        with self.assertRaises(FormatError) as caught:
            jpeg.rebuild(with_segments(encode(gradient()), app(0xEA, curve + MARKER)))
        self.assertEqual(caught.exception.key, "unsupported_part")

    def test_iso_gain_map_metadata_keeps_the_fields_of_its_layout_only(self):
        namespace = b"urn:iso:std:iso:ts:21496:-1\0"

        def kept(metadata):
            rebuilt = self.assertRebuilt(with_segments(encode(gradient()), app(0xE2, namespace + metadata)))
            return next(p for m, _, _, p in jpeg.segments(rebuilt) if m == 0xE2 and p.startswith(namespace))

        # As they are: the primary image's version fields alone, metadata of one and of
        # three channels, reserved flag bits set (as phones do), a newer writer version.
        reserved = iso_metadata()[:4] + bytes([iso_metadata()[4] | 0x33]) + iso_metadata()[5:]
        newer = struct.pack(">HH", 0, 1) + iso_metadata()[4:]
        for metadata in (bytes(4), iso_metadata(), iso_metadata(channels=3, common=True), reserved, newer):
            with self.subTest(flags=metadata[4:5]):
                self.assertEqual(kept(metadata), namespace + metadata)
        # Anything after the fields, which no decoder reads, is dropped.
        for metadata in (iso_metadata(tail=MARKER), newer + MARKER):
            with self.subTest(tail=len(metadata)):
                self.assertEqual(kept(metadata), namespace + metadata[:-len(MARKER)])
        # Cut short, of a minimum version a decoder of version 0 may not read, or with zero denominators.
        unreadable = struct.pack(">HH", 1, 1) + iso_metadata()[4:]
        for metadata in (iso_metadata()[:-1], unreadable, bytes(20), bytes(61)):
            with self.subTest(size=len(metadata)), self.assertRaises(FormatError) as caught:
                jpeg.rebuild(with_segments(encode(gradient()), app(0xE2, namespace + metadata)))
            self.assertEqual(caught.exception.key, "unsupported_part")

    def test_unknown_and_damaged_structures_are_refused(self):
        data = encode(gradient())
        with self.assertRaises(FormatError) as caught:
            jpeg.rebuild(with_segments(data, app(0xF7, b"\0" * 8)))  # a JPEG-LS frame header
        self.assertEqual(caught.exception.key, "unsupported_part")
        half_profile = app(0xE2, b"ICC_PROFILE\0\x01\x02" + bytes(64))
        for damaged in (data[:-2], data[:40], with_segments(data, half_profile), b"\xff\xd8\xff\xe1\0\x01"):
            with self.subTest(size=len(damaged)), self.assertRaises(FormatError) as caught:
                jpeg.rebuild(damaged)
            self.assertEqual(caught.exception.key, "damaged")

    def test_verify_notices_a_tampered_result(self):
        data = with_segments(encode(gradient()), app(0xE1, exif_block()))
        rebuilt = jpeg.rebuild(data)
        for result in (rebuilt + MARKER, with_segments(rebuilt, app(0xFE, b"x")),
                       rebuilt.replace(b"\xff\xda", b"\xff\xda", 1)[:-40] + rebuilt[-38:]):
            with self.subTest(size=len(result)), self.assertRaises((VerificationError, FormatError)):
                jpeg.verify(data, result)


class MultiPictureTests(unittest.TestCase):
    def frames(self):
        primary = with_segments(encode(gradient()), app(0xE1, exif_block()))
        gain_map = with_segments(encode(gradient("L", (12, 8))),
                                 app(0xE1, b"http://ns.adobe.com/xap/1.0/\0" + HDR_XMP))
        return [primary, gain_map]

    def test_fresh_index_without_image_ids(self):
        original = mpf(self.frames(), extra_tags=[(0xB003, MARKER * 2)])
        with self.assertRaises(VerificationError):
            jpeg.images(original, strict=True)
        rebuilt = jpeg.rebuild(original)
        jpeg.verify(original, rebuilt)
        self.assertNotIn(MARKER, rebuilt)
        self.assertEqual(decoded(rebuilt), decoded(original))
        entries, frames = jpeg.images(rebuilt, strict=True)
        self.assertEqual(len(frames), 2)
        self.assertIn(b'hdrgm:GainMapMax="2.5"', frames[1])

    def test_data_between_or_after_images_is_dropped(self):
        original = mpf(self.frames()) + MARKER
        rebuilt = jpeg.rebuild(original)
        jpeg.verify(original, rebuilt)
        self.assertNotIn(MARKER, rebuilt)
        with self.assertRaises(VerificationError):
            jpeg.verify(original, rebuilt + MARKER)

    def test_previews_are_dropped(self):
        # A preview of the picture before it was cropped, listed as the primary
        # image's dependent: the copy is a plain JPEG of the primary image.
        primary, gain_map = self.frames()
        preview = encode(gradient(size=(40, 16)))
        original = mpf([primary, preview], entries=[(0xA0030000, 2, 0), (0x40010001, 0, 0)])
        rebuilt = jpeg.rebuild(original)
        jpeg.verify(original, rebuilt)
        self.assertEqual(jpeg.images(rebuilt, strict=True)[0], [])
        self.assertNotIn(b"MPF\0", rebuilt)
        self.assertNotIn(jpeg.coding_hash(preview), [jpeg.coding_hash(frame) for frame in jpeg.images(rebuilt)[1]])
        self.assertEqual(decoded(rebuilt), decoded(primary))
        # With a gain map as well, the gain map stays and the links are renumbered.
        for primary_entry, expected in (((0x80030000, 2, 0), (0x030000, 0, 0)),
                                        ((0x80030000, 3, 0), (0x80030000, 2, 0))):
            with self.subTest(primary_entry=primary_entry):
                original = mpf([primary, preview, gain_map],
                               entries=[primary_entry, (0x40010002, 0, 0), (0, 0, 0)])
                rebuilt = jpeg.rebuild(original)
                jpeg.verify(original, rebuilt)
                entries, frames = jpeg.images(rebuilt, strict=True)
                self.assertEqual(entries, [expected, (0, 0, 0)])
                self.assertEqual([jpeg.coding_hash(frame) for frame in frames],
                                 [jpeg.coding_hash(primary), jpeg.coding_hash(gain_map)])
        # A result that kept the preview fails verification.
        entries, frames = jpeg.images(original)
        kept_preview = jpeg.join_mpf(entries, [b"".join(jpeg.expected_segments(frame)) for frame in frames])
        with self.assertRaises(VerificationError):
            jpeg.verify(original, kept_preview)

    def test_a_photo_with_a_preview_is_cleaned_end_to_end(self):
        # Through the whole pipeline, whose pixel check decodes every frame it keeps.
        primary, gain_map = self.frames()
        preview = encode(gradient(size=(40, 16)))
        cases = [([primary, preview], [(0xA0030000, 2, 0), (0x40010001, 0, 0)]),
                 ([primary, preview, gain_map], [(0x80030000, 2, 0), (0x40010002, 0, 0), (0, 0, 0)])]
        with tempfile.TemporaryDirectory(prefix="jpeg-") as folder:
            for index, (frames, entries) in enumerate(cases):
                with self.subTest(images=len(frames)):
                    path = Path(folder, "DSC_000%d.JPG" % index)
                    path.write_bytes(mpf(frames, entries=entries))
                    output = decoded(core.clean(str(path)).read_bytes())
                    self.assertEqual(output, decoded(primary) + (decoded(gain_map) if len(frames) == 3 else []))

    def test_other_extra_images_are_refused(self):
        primary, gain_map = self.frames()
        second_view = encode(gradient())
        wide_gain_map = with_segments(encode(gradient("L", (24, 8))),
                                      app(0xE1, b"http://ns.adobe.com/xap/1.0/\0" + HDR_XMP))
        for original in (pillow_mpo(2), pillow_mpo(3),  # images of undefined type
                         mpf([primary, second_view], entries=[(0x020002, 0, 0), (0x020002, 0, 0)]),  # a stereo pair
                         mpf([primary, wide_gain_map])):  # a "gain map" showing more than the photo
            with self.subTest(size=len(original)), self.assertRaises(FormatError) as caught:
                jpeg.rebuild(original)
            self.assertEqual(caught.exception.key, "unsupported_part")

    def test_a_stale_size_for_the_first_image_is_not_relied_on(self):
        # Adding metadata to the first image, ExifTool updates the other images'
        # offsets but leaves the first image's listed size as it was.
        original = bytearray(mpf(self.frames()))
        order, _, images = image_list(original)
        struct.pack_into(order + "I", original, images + 4, 100)
        original = bytes(original)
        with self.assertRaises(VerificationError):
            jpeg.images(original, strict=True)
        rebuilt = jpeg.rebuild(original)
        jpeg.verify(original, rebuilt)
        self.assertEqual(decoded(rebuilt), decoded(original))

    def test_an_image_listed_outside_the_file_is_refused(self):
        data = bytearray(pillow_mpo(2))
        order, _, images = image_list(data)
        struct.pack_into(order + "I", data, images + 16 + 8, 0xFFFFFFF0)
        with self.assertRaises(FormatError) as caught:
            jpeg.rebuild(bytes(data))
        self.assertEqual(caught.exception.key, "damaged")

    def test_cleaning_without_exiftool(self):
        with tempfile.TemporaryDirectory(prefix="jpeg-") as folder:
            path = Path(folder, "IMG 0001.JPG")
            path.write_bytes(mpf(self.frames(), extra_tags=[(0xB003, MARKER)]))
            output = core.clean(str(path))
            self.assertEqual(output.name, "IMG 0001_clean.JPG")
            self.assertNotIn(MARKER, output.read_bytes())
            self.assertEqual(decoded(output.read_bytes()), decoded(path.read_bytes()))


if __name__ == "__main__":
    unittest.main()
