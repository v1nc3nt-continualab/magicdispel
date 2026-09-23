"""TIFF rebuilding: kept tags and image data, dropped directories, refused variants."""
import io
import struct
import unittest

from PIL import Image

from magicdispel.errors import FormatError, VerificationError
from magicdispel.formats import tiff

MARKER = "MD_TIFF_PRIVATE"


def gradient(mode="RGB", size=(20, 14), shift=0):
    image = Image.new("RGB", size)
    image.putdata([((x * 12 + shift) % 256, y * 18, (x * y) % 256) for y in range(size[1]) for x in range(size[0])])
    return image.convert(mode)


def encode(image, **options):
    buffer = io.BytesIO()
    image.save(buffer, "TIFF", **options)
    return buffer.getvalue()


def private_exif():
    tags = Image.Exif()
    tags[0x0112], tags[0x010E], tags[0x013B], tags[0x0131] = 6, MARKER, MARKER, "Editor " + MARKER
    tags.get_ifd(0x8769)[0x9003] = "2026:09:23 17:40:00"
    gps = tags.get_ifd(0x8825)
    gps[1], gps[2] = "N", (40.0, 42.0, 51.0)
    return tags


def decoded(data):
    with Image.open(io.BytesIO(data)) as image:
        pages = []
        for index in range(getattr(image, "n_frames", 1)):
            image.seek(index)
            pages.append((image.mode, image.size, image.tobytes(), image.info.get("dpi")))
        return pages


def with_values(data, tag, kind, values):
    """Point a tag of the first directory at new values appended to the file."""
    data = bytearray(data)
    order = "<" if data[:2] == b"II" else ">"
    first = struct.unpack_from(order + "I", data, 4)[0]
    for index in range(struct.unpack_from(order + "H", data, first)[0]):
        entry = first + 2 + 12 * index
        if struct.unpack_from(order + "H", data, entry)[0] == tag:
            struct.pack_into(order + "HHII", data, entry, tag, kind, len(values), len(data))
            data += struct.pack(order + ("%dH" if kind == 3 else "%dI") % len(values), *values)
    return bytes(data)


def patch_tag(data, tag, value):
    """Overwrite the inline value of a tag in the first directory."""
    data = bytearray(data)
    order = "<" if data[:2] == b"II" else ">"
    first = struct.unpack_from(order + "I", data, 4)[0]
    for index in range(struct.unpack_from(order + "H", data, first)[0]):
        entry = first + 2 + 12 * index
        if struct.unpack_from(order + "H", data, entry)[0] == tag:
            struct.pack_into(order + "H", data, entry + 8, value)
    return bytes(data)


class TiffTests(unittest.TestCase):
    def assertRebuilt(self, data):
        rebuilt = tiff.rebuild(data)
        tiff.verify(data, rebuilt)
        self.assertEqual(decoded(rebuilt), decoded(data))
        self.assertNotIn(MARKER.encode(), rebuilt)
        return rebuilt

    def test_private_and_descriptive_tags_are_dropped(self):
        # Pillow writes these tags into the page itself (it leaves out EXIF and GPS
        # directories, which test_formats adds with ExifTool when it is installed).
        tags = private_exif()
        tags[65000] = MARKER
        data = encode(gradient(), exif=tags, dpi=(300, 300))
        _, pages = tiff.parse(data)
        self.assertTrue({0x0112, 0x010E, 0x0131, 0x013B, 65000} <= set(pages[0]["tags"]))
        rebuilt = self.assertRebuilt(data)
        _, pages = tiff.parse(rebuilt)
        self.assertLessEqual(set(pages[0]["tags"]), tiff.KEPT)
        with Image.open(io.BytesIO(rebuilt)) as image:
            self.assertEqual(image.getexif()[0x0112], 6)
            self.assertEqual(tuple(round(v) for v in image.info["dpi"]), (300, 300))

    def test_pages_compressions_and_sample_layouts(self):
        pages = [gradient(shift=n * 60) for n in range(3)]
        cases = [encode(pages[0], save_all=True, append_images=pages[1:], compression="tiff_lzw")]
        for compression in ("raw", "tiff_adobe_deflate", "packbits", "jpeg"):
            cases.append(encode(gradient(), compression=compression))
        for mode in ("RGBA", "CMYK", "I;16", "L", "1"):
            cases.append(encode(gradient(mode) if mode != "I;16" else Image.new("I;16", (9, 7), 40000)))
        for data in cases:
            with self.subTest(size=len(data)):
                self.assertRebuilt(data)
        big_endian = encode(Image.new("I;16B", (9, 7), 1234))
        self.assertEqual(big_endian[:2], b"MM")
        self.assertEqual(self.assertRebuilt(big_endian)[:2], b"MM")

    def test_variants_that_cannot_be_rebuilt_safely_are_refused(self):
        with self.assertRaises(FormatError) as caught:
            tiff.rebuild(b"II+\0\x08\0\0\0" + bytes(16))
        self.assertEqual(caught.exception.key, "unsupported_variant")
        with self.assertRaises(FormatError) as caught:
            tiff.rebuild(patch_tag(encode(gradient()), tiff.COMPRESSION, tiff.OLD_JPEG))
        self.assertEqual(caught.exception.key, "unsupported_variant")
        buffer = io.BytesIO()
        gradient().save(buffer, "JPEG", exif=private_exif())  # a JPEG stream with an APP1 segment
        with self.assertRaises(FormatError) as caught:
            tiff.check_jpeg_block(buffer.getvalue())
        self.assertEqual(caught.exception.key, "unsupported_part")
        for damaged in (b"II*\0\xff\xff\0\0", encode(gradient())[:40], b"XX*\0\x08\0\0\0"):
            with self.subTest(damaged=damaged[:8]), self.assertRaises(FormatError):
                tiff.rebuild(damaged)

    def test_raw_photos_are_refused_rather_than_reduced_to_their_preview(self):
        dng = encode(gradient(), tiffinfo={254: 1, 50706: b"\x01\x04\x00\x00"})
        cr2 = b"II*\0\x10\0\0\0CR\x02\0" + bytes(16)
        for data in (dng, cr2):
            with self.subTest(size=len(data)):
                with self.assertRaises(FormatError) as caught:
                    tiff.rebuild(data)
                self.assertEqual(caught.exception.key, "raw_photo")

    def test_extra_strips_and_values_are_refused(self):
        # Hidden bytes in a strip the image does not need, in an uncompressed strip
        # longer than its rows, or in more values than a tag has.
        data = encode(gradient(), tiffinfo={278: 7})  # two strips of 7 rows
        _, pages = tiff.parse(data)
        offsets = [int.from_bytes(pages[0]["tags"][273][1][n:n + 4], "little") for n in (0, 4)]
        sizes = [int.from_bytes(pages[0]["tags"][279][1][n:n + 4], "little") for n in (0, 4)]
        hidden = len(data)
        extra_strip = with_values(with_values(data + MARKER.encode(), 273, 4, offsets + [hidden]),
                                  279, 4, sizes + [len(MARKER)])
        longer_strip = with_values(data + MARKER.encode(), 279, 4, [sizes[0], sizes[1] + len(MARKER)])
        longer_strip = with_values(longer_strip, 273, 4, [offsets[0], hidden - sizes[1]])
        extra_value = with_values(data, 262, 3, [2, 0x4D44])  # the color interpretation has one value
        for data, key in ((extra_strip, "damaged"), (longer_strip, "extra_image_data"), (extra_value, "damaged")):
            with self.subTest(key=key):
                with self.assertRaises(FormatError) as caught:
                    tiff.rebuild(data)
                self.assertEqual(caught.exception.key, key)

    def test_verify_notices_a_tampered_result(self):
        data = encode(gradient())
        rebuilt = tiff.rebuild(data)
        _, pages = tiff.parse(rebuilt)
        strip = rebuilt.index(pages[0]["blocks"][0])
        flipped = bytearray(rebuilt)
        flipped[strip] ^= 0xFF
        for result in (bytes(flipped), rebuilt + b"\0\0", rebuilt + b"\x01"):
            with self.subTest(size=len(result)), self.assertRaises(VerificationError):
                tiff.verify(data, result)


if __name__ == "__main__":
    unittest.main()
