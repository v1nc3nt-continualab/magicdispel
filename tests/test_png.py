"""PNG, APNG and BMP rebuilding: what is kept, dropped and refused."""
import io
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from PIL import Image, ImageCms, PngImagePlugin

from magicdispel import core, exif, icc
from magicdispel.errors import FormatError, VerificationError
from magicdispel.formats import bmp, png

MARKER = b"MD_PNG_PRIVATE"


def encode(image, format="PNG", **options):
    buffer = io.BytesIO()
    image.save(buffer, format, **options)
    return buffer.getvalue()


def gradient(mode="RGB", size=(7, 5)):
    image = Image.new("RGB", size)
    image.putdata([(x * 30, y * 50, (x + y) * 20) for y in range(size[1]) for x in range(size[0])])
    return image.convert(mode)


def kinds(data):
    return [kind for kind, _, _ in png.chunks(data)]


def insert(data, kind, payload, before=b"IDAT"):
    """Add a chunk in front of the first `before` chunk."""
    position = len(png.SIGNATURE)
    for existing, body, _ in png.chunks(data):
        if existing == before:
            break
        position += 12 + len(body)
    return data[:position] + png.serialize(kind, payload) + data[position:]


def replace(data, kind, payload):
    """Swap the payload of the first `kind` chunk, keeping a valid CRC."""
    parts = [(k, payload if k == kind else p) for k, p, _ in png.chunks(data)]
    return png.SIGNATURE + b"".join(png.serialize(k, p) for k, p in parts)


def decoded(data):
    with Image.open(io.BytesIO(data)) as image:
        frames = []
        for index in range(getattr(image, "n_frames", 1)):
            image.seek(index)
            frames.append((image.mode, image.size, image.info.get("duration"), image.tobytes()))
        return image.info.get("dpi"), image.info.get("loop"), frames


def adam7_png(image):
    """An interlaced PNG, which Pillow cannot write, built from RGB pixels."""
    width, height = image.size
    raw = b""
    for column, row, column_step, row_step in png.ADAM7:
        for y in range(row, height, row_step):
            line = b"".join(bytes(image.getpixel((x, y))) for x in range(column, width, column_step))
            if line:
                raw += b"\0" + line
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 1)
    return (png.SIGNATURE + png.serialize(b"IHDR", header)
            + png.serialize(b"IDAT", zlib.compress(raw)) + png.serialize(b"IEND", b""))


class RebuildTests(unittest.TestCase):
    def assertRebuilt(self, data):
        rebuilt = png.rebuild(data)
        png.verify(data, rebuilt)
        self.assertEqual(decoded(rebuilt), decoded(data))
        return rebuilt

    def test_text_time_private_and_trailing_data_are_dropped(self):
        info = PngImagePlugin.PngInfo()
        info.add_text("Author", MARKER.decode())
        info.add_text("Comment", MARKER.decode(), zip=True)
        info.add_itxt("Location", MARKER.decode(), zip=True)
        data = encode(gradient("RGBA"), pnginfo=info, dpi=(144, 144))
        for kind, payload in ((b"sRGB", b"\0"), (b"tIME", struct.pack(">HBBBBB", 2026, 9, 23, 17, 40, 1)),
                              (b"prVt", MARKER), (b"caBX", MARKER), (b"iDOT", bytes(28))):
            data = insert(data, kind, payload)
        rebuilt = self.assertRebuilt(data + MARKER)
        self.assertNotIn(MARKER, rebuilt)
        self.assertEqual(kinds(rebuilt), [kind for kind in kinds(data) if kind in png.KEPT])
        self.assertIn(b"pHYs", kinds(rebuilt))

    def test_profile_is_sanitized(self):
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        rebuilt = self.assertRebuilt(encode(gradient(), icc_profile=profile))
        payload = dict((k, p) for k, p, _ in png.chunks(rebuilt))[b"iCCP"]
        self.assertTrue(payload.startswith(b"Clean\0\0"))
        self.assertEqual(zlib.decompress(payload[7:]), icc.sanitize(profile))

    def test_exif_is_reduced_to_the_orientation(self):
        for orientation in (6, 1):
            with self.subTest(orientation=orientation):
                tags = Image.Exif()
                tags[0x0112], tags[0x010F], tags[0x0131] = orientation, MARKER.decode(), "Editor " + MARKER.decode()
                rebuilt = self.assertRebuilt(encode(gradient(), exif=tags.tobytes()))
                self.assertNotIn(MARKER, rebuilt)
                chunks = dict((k, p) for k, p, _ in png.chunks(rebuilt))
                if orientation == 1:
                    self.assertNotIn(b"eXIf", chunks)
                else:
                    self.assertEqual(chunks[b"eXIf"], exif.build(exif.DisplayFields(orientation=6)))
                    with Image.open(io.BytesIO(rebuilt)) as image:
                        self.assertEqual(image.getexif()[0x0112], 6)

    def test_animation_is_kept(self):
        frames = [gradient("RGBA"), gradient("RGBA").rotate(180), Image.new("RGBA", (7, 5), (0, 0, 0, 0))]
        info = PngImagePlugin.PngInfo()
        info.add_text("Software", MARKER.decode())
        data = encode(frames[0], save_all=True, append_images=frames[1:], duration=[40, 80, 120], loop=3,
                      pnginfo=info)
        self.assertTrue(png.is_animated(data))
        rebuilt = self.assertRebuilt(data)
        self.assertNotIn(MARKER, rebuilt)
        self.assertEqual(decoded(rebuilt)[1], 3)
        self.assertEqual(len(decoded(rebuilt)[2]), 3)

    def test_interlaced_and_palette_images(self):
        self.assertRebuilt(adam7_png(gradient(size=(13, 9))))
        self.assertRebuilt(encode(gradient("P"), transparency=0))
        self.assertRebuilt(encode(gradient("L").convert("1")))
        self.assertRebuilt(encode(Image.new("I;16", (4, 3), 40000)))

    def test_bytes_hidden_in_image_data_are_refused(self):
        data = encode(gradient())
        idat = dict((k, p) for k, p, _ in png.chunks(data))[b"IDAT"]
        extra_rows = zlib.compress(zlib.decompress(idat) + b"\0" + bytes(21))
        for tampered in (replace(data, b"IDAT", idat + MARKER), replace(data, b"IDAT", extra_rows)):
            with self.assertRaises(FormatError) as caught:
                png.rebuild(tampered)
            self.assertEqual(caught.exception.key, "extra_image_data")

    def test_unknown_critical_chunk_is_refused(self):
        with self.assertRaises(FormatError) as caught:
            png.rebuild(insert(encode(gradient()), b"ZZZZ", MARKER))
        self.assertEqual(caught.exception.key, "unsupported_part")

    def test_damaged_files_are_refused(self):
        data = encode(gradient())
        bad_crc = bytearray(data)
        bad_crc[-17] ^= 1  # inside the IDAT CRC
        truncated_stream = replace(data, b"IDAT", dict((k, p) for k, p, _ in png.chunks(data))[b"IDAT"][:-6])
        for damaged in (data[:-20], bytes(bad_crc), insert(data, b"pHYs", bytes(10)), truncated_stream,
                        png.SIGNATURE + png.serialize(b"sRGB", b"\0") + data[8:], b"not a png"):
            with self.subTest(size=len(damaged)), self.assertRaises(FormatError) as caught:
                png.rebuild(damaged)
            self.assertEqual(caught.exception.key, "damaged")

    def test_damaged_chunk_that_is_dropped_anyway(self):
        clean = encode(gradient())
        broken = insert(clean, b"tEXt", b"Comment\0" + MARKER).replace(MARKER, MARKER[:-1] + b"X")
        rebuilt = png.rebuild(broken)  # the CRC no longer matches, but the chunk goes anyway
        png.verify(broken, rebuilt)
        self.assertEqual(rebuilt, png.rebuild(clean))
        # Pillow rejects the original outright, so the command refuses the file
        # rather than save a copy it cannot compare with the original.
        with tempfile.TemporaryDirectory(prefix="png-") as folder:
            path = Path(folder, "broken.png")
            path.write_bytes(broken)
            with self.assertRaises(FormatError) as caught:
                core.clean(str(path))
            self.assertEqual(caught.exception.key, "damaged")
            self.assertEqual(sorted(p.name for p in Path(folder).iterdir()), ["broken.png"])

    def test_cleaning_without_exiftool(self):
        with tempfile.TemporaryDirectory(prefix="png-") as folder:
            path = Path(folder, "截屏 1.png")
            info = PngImagePlugin.PngInfo()
            info.add_text("Author", MARKER.decode())
            path.write_bytes(encode(gradient(), pnginfo=info, dpi=(144, 144)))
            output = core.clean(str(path))
            self.assertEqual(output.name, "截屏 1_clean.png")
            self.assertNotIn(MARKER, output.read_bytes())
            self.assertEqual(decoded(output.read_bytes()), decoded(path.read_bytes()))

    def test_verify_notices_a_tampered_result(self):
        data = encode(gradient())
        rebuilt = png.rebuild(data)
        tampered = [rebuilt + MARKER, insert(rebuilt, b"tEXt", b"Comment\0x"),
                    replace(rebuilt, b"IDAT", zlib.compress(bytes(len(zlib.decompress(
                        dict((k, p) for k, p, _ in png.chunks(rebuilt))[b"IDAT"])))))]
        for result in tampered:
            with self.subTest(size=len(result)), self.assertRaises(VerificationError):
                png.verify(data, result)


class BmpTests(unittest.TestCase):
    def test_bmp_becomes_png_with_same_pixels_and_dpi(self):
        for mode in ("RGB", "P", "L", "1"):
            with self.subTest(mode=mode):
                data = encode(gradient(mode), "BMP", dpi=(96, 96))
                rebuilt = bmp.rebuild(data)
                bmp.verify(data, rebuilt)
                with Image.open(io.BytesIO(rebuilt)) as image:
                    self.assertEqual(image.format, "PNG")
                    self.assertEqual(tuple(round(v) for v in image.info["dpi"]), (96, 96))


if __name__ == "__main__":
    unittest.main()
