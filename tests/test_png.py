"""PNG, APNG and BMP rebuilding: what is kept, dropped and refused."""
import io
import random
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

    def test_palette_and_transparency_hold_nothing_extra(self):
        rgb, palette = encode(gradient()), encode(gradient("P"))
        entries = len(dict((k, p) for k, p, _ in png.chunks(palette))[b"PLTE"]) // 3
        for tampered in (insert(rgb, b"tRNS", bytes(6) + MARKER), insert(palette, b"tRNS", bytes(entries + 1)),
                         replace(palette, b"PLTE", bytes(3 * entries + 1)), insert(encode(gradient("L")), b"PLTE", bytes(3))):
            with self.subTest(size=len(tampered)):
                with self.assertRaises(FormatError) as caught:
                    png.rebuild(tampered)
                self.assertEqual(caught.exception.key, "damaged")

    def test_a_chunk_that_may_come_once_is_kept_once_and_only_where_it_belongs(self):
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        data = encode(gradient("RGBA"), dpi=(144, 144), icc_profile=profile)
        icc_chunk = dict((k, p) for k, p, _ in png.chunks(data))[b"iCCP"]
        dozen = data
        for _ in range(12):
            dozen = insert(dozen, b"sRGB", b"\0")
        late = insert(insert(data, b"gAMA", bytes(4), b"IEND"), b"pHYs", bytes(9), b"IEND")
        one_srgb = png.rebuild(insert(data, b"sRGB", b"\0"))
        cases = {
            "another pHYs": (insert(data, b"pHYs", struct.pack(">IIB", 1, 1, 1)), png.rebuild(data)),
            "iCCP twice": (insert(data, b"iCCP", icc_chunk), png.rebuild(data)),
            "sRGB by the dozen": (dozen, one_srgb),
            "gAMA and pHYs after the image": (late, png.rebuild(data)),
            "a suggested palette in an RGBA image": (insert(data, b"PLTE", bytes(12)), png.rebuild(data)),
        }
        for name, (changed, expected) in cases.items():
            with self.subTest(name):
                rebuilt = png.rebuild(changed)
                png.verify(changed, rebuilt)
                self.assertEqual(rebuilt, expected)

    def test_image_data_is_framed_the_one_way(self):
        # Noise that takes 200,000 bytes: cut into chunks of 7 bytes or of a megabyte, it is the same image.
        image = Image.frombytes("RGB", (300, 250), random.Random(1).randbytes(300 * 250 * 3))
        data = encode(image)
        stream = b"".join(p for k, p, _ in png.chunks(data) if k == b"IDAT")
        self.assertGreater(len(stream), 2 * png.FRAME)
        plain = self.assertRebuilt(data)
        self.assertEqual([len(p) for k, p, _ in png.chunks(plain) if k == b"IDAT"],
                         [png.FRAME] * (len(stream) // png.FRAME) + [len(stream) % png.FRAME])
        for size in (7, 1000, len(stream)):
            head = [(k, p) for k, p, _ in png.chunks(data) if k == b"IHDR"]
            cut = [(b"IDAT", stream[n:n + size]) for n in range(0, len(stream), size)]
            reframed = png.SIGNATURE + b"".join(png.serialize(k, p) for k, p in head + cut + [(b"IEND", b"")])
            with self.subTest(size=size):
                self.assertEqual(png.rebuild(reframed), plain)
        self.assertEqual(png.rebuild(plain), plain)

    def test_animation_frames_are_numbered_afresh(self):
        frames = [gradient("RGBA"), gradient("RGBA").rotate(180), Image.new("RGBA", (7, 5), (0, 0, 0, 0))]
        data = encode(frames[0], save_all=True, append_images=frames[1:], duration=[40, 80, 120], loop=3)
        plain = self.assertRebuilt(data)
        numbers = [struct.unpack_from(">I", p)[0] for k, p, _ in png.chunks(plain) if k in (b"fcTL", b"fdAT")]
        self.assertEqual(numbers, list(range(len(numbers))))
        # The numbers are what a decoder checks, and they carry nothing when they are checked.
        shuffled = [(k, struct.pack(">I", 1000 - n) + p[4:]) if k in (b"fcTL", b"fdAT") else (k, p)
                    for n, (k, p, _) in enumerate(png.chunks(data))]
        changed = png.SIGNATURE + b"".join(png.serialize(k, p) for k, p in shuffled)
        self.assertEqual(png.rebuild(changed), plain)
        # Frames of an animation that acTL does not declare are not frames.
        without = png.SIGNATURE + b"".join(png.serialize(k, p) for k, p, _ in png.chunks(data) if k != b"acTL")
        self.assertEqual(kinds(png.rebuild(without)), [b"IHDR", b"IDAT", b"IEND"])

    def test_chunks_hold_what_their_standard_defines(self):
        rgb, palette, gray = encode(gradient()), encode(gradient("P")), encode(gradient("L"))
        entries = len(dict((k, p) for k, p, _ in png.chunks(palette))[b"PLTE"]) // 3
        refused = (
            ("background of the wrong size", rgb, b"bKGD", bytes(2)),
            ("background out of range", palette, b"bKGD", bytes([entries])),
            ("gray level out of range", gray, b"bKGD", b"\x01\x00"),
            ("bits that are not there", rgb, b"sBIT", bytes([8, 8, 9])),
            ("bits of another color type", rgb, b"sBIT", bytes(4)),
            ("a rendering intent that is not one", rgb, b"sRGB", b"\x09"),
            ("a matrix in an RGB image", rgb, b"cICP", bytes([1, 13, 1, 1])),
            ("a unit that is not one", rgb, b"pHYs", struct.pack(">IIB", 1, 1, 7)),
        )
        for name, data, kind, payload in refused:
            with self.subTest(name), self.assertRaises(FormatError) as caught:
                png.rebuild(insert(data, kind, payload))
            self.assertEqual(caught.exception.key, "damaged")
        header = dict((k, p) for k, p, _ in png.chunks(rgb))[b"IHDR"]
        with self.assertRaises(FormatError) as caught:  # a second header
            png.rebuild(insert(rgb, b"IHDR", header))
        self.assertEqual(caught.exception.key, "damaged")
        allowed = ((b"bKGD", struct.pack(">HHH", 1, 2, 3)), (b"sBIT", bytes([5, 6, 7])), (b"sRGB", b"\x03"),
                   (b"cICP", bytes([1, 13, 0, 1])), (b"pHYs", struct.pack(">IIB", 1, 1, 1)))
        for kind, payload in allowed:
            with self.subTest(kind=kind):
                self.assertIn(kind, kinds(self.assertRebuilt(insert(rgb, kind, payload))))

    def test_huge_images_are_refused_before_inflating(self):
        header = struct.pack(">IIBBBBB", 30000, 30000, 8, 2, 0, 0, 0)
        data = png.SIGNATURE + png.serialize(b"IHDR", header) + png.serialize(b"IDAT", zlib.compress(b"")) \
            + png.serialize(b"IEND", b"")
        with self.assertRaises(FormatError) as caught:
            png.rebuild(data)
        self.assertEqual(caught.exception.key, "too_large")

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
            self.assertEqual(caught.exception.key, "undecodable")
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
    @staticmethod
    def version_5(color_space, profile=b""):
        """A BMP of 4 x 2 pixels with a version 5 header, and `profile` after the pixels."""
        rows = b"".join(bytes([n * 40, 20, 200 - n * 40]) for n in range(4)) * 2
        header = struct.pack("<IiiHHIIiiII", 124, 4, 2, 1, 24, 0, len(rows), 2835, 2835, 0, 0)
        header += struct.pack("<IIII", 0xFF0000, 0xFF00, 0xFF, 0) + struct.pack("<I", color_space) + bytes(48)
        header += struct.pack("<IIII", 8, 124 + len(rows), len(profile), 0)
        return b"BM" + struct.pack("<IHHI", 14 + 124 + len(rows) + len(profile), 0, 0, 14 + 124) + header + rows + profile

    def test_a_profile_a_version_5_header_embeds_is_kept(self):
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        rebuilt = bmp.rebuild(self.version_5(0x4D424544, profile))
        chunks = dict((k, p) for k, p, _ in png.chunks(rebuilt))
        self.assertEqual(zlib.decompress(chunks[b"iCCP"][7:]), icc.sanitize(profile))
        self.assertNotIn(b"iCCP", dict((k, p) for k, p, _ in png.chunks(bmp.rebuild(self.version_5(0x73524742)))))
        for damaged in (self.version_5(0x4D424544), self.version_5(0x4D424544, profile)[:-10]):
            with self.assertRaises(FormatError) as caught:
                bmp.rebuild(damaged)
            self.assertEqual(caught.exception.key, "damaged")

    def test_a_profile_in_a_file_of_its_own_cannot_be_kept(self):
        with self.assertRaises(FormatError) as caught:
            bmp.rebuild(self.version_5(0x4C494E4B, b"C:\\colors\\Private Name.icm\0"))
        self.assertEqual(caught.exception.key, "unsupported_variant")

    def test_run_length_of_four_bits_is_refused_as_pillow_decodes_it_wrongly(self):
        header = struct.pack("<IiiHHIIiiII", 40, 4, 1, 1, 4, 2, 6, 2835, 2835, 16, 0)
        palette = b"".join(bytes([n * 16, 0, 255 - n * 16, 0]) for n in range(16))
        data = b"BM" + struct.pack("<IHHI", 14 + 40 + 64 + 6, 0, 0, 14 + 40 + 64) + header + palette \
            + bytes([4, 0x12, 0, 0, 0, 1])
        with self.assertRaises(FormatError) as caught:
            bmp.rebuild(data)
        self.assertEqual(caught.exception.key, "unsupported_variant")

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
