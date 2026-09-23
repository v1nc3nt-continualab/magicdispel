"""WebP and GIF rebuilding: what is kept, dropped and refused."""
import io
import struct
import unittest

from PIL import Image, ImageCms

from magicdispel import exif, icc
from magicdispel.errors import FormatError, VerificationError
from magicdispel.formats import gif, webp

MARKER = b"MD_WEBP_GIF_PRIVATE"
PROFILE = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def gradient(mode="RGB", size=(20, 12), shift=0):
    image = Image.new("RGB", size)
    image.putdata([((x * 12 + shift) % 256, y * 20, (x + y) * 9) for y in range(size[1]) for x in range(size[0])])
    return image.convert(mode)


def encode(image, format, **options):
    buffer = io.BytesIO()
    image.save(buffer, format, **options)
    return buffer.getvalue()


def decoded(data):
    with Image.open(io.BytesIO(data)) as image:
        frames = []
        for index in range(getattr(image, "n_frames", 1)):
            image.seek(index)
            frames.append((image.size, image.info.get("duration"), image.convert("RGBA").tobytes()))
        return image.info.get("loop"), frames


def private_exif():
    tags = Image.Exif()
    tags[0x0112], tags[0x010F] = 6, MARKER.decode()
    return tags.tobytes()


def riff(parts, trailing=b""):
    return webp.serialize(parts) + trailing


class WebPTests(unittest.TestCase):
    def assertRebuilt(self, data):
        rebuilt = webp.rebuild(data)
        webp.verify(data, rebuilt)
        self.assertEqual(decoded(rebuilt), decoded(data))
        self.assertNotIn(MARKER, rebuilt)
        return rebuilt

    def test_simple_files_are_unchanged(self):
        for options in ({"quality": 80}, {"lossless": True}):
            with self.subTest(**options):
                data = encode(gradient(), "WEBP", **options)
                self.assertEqual(self.assertRebuilt(data), data)

    def test_metadata_and_unknown_chunks_are_dropped(self):
        data = encode(gradient(), "WEBP", quality=80, icc_profile=PROFILE, exif=private_exif(),
                      xmp=b"<x:xmpmeta xmlns:x='adobe:ns:meta/'>" + MARKER + b"</x:xmpmeta>")
        parts = webp.chunks(data) + [(b"SECR", MARKER)]
        rebuilt = self.assertRebuilt(riff(parts, trailing=MARKER))
        found = dict(webp.chunks(rebuilt))
        self.assertEqual([kind for kind, _ in webp.chunks(rebuilt)], [b"VP8X", b"ICCP", b"VP8 ", b"EXIF"])
        self.assertEqual(found[b"ICCP"], icc.sanitize(PROFILE))
        self.assertEqual(found[b"EXIF"], exif.build(exif.DisplayFields(orientation=6)))
        self.assertEqual(found[b"VP8X"][:4], bytes([webp.ICC | webp.EXIF, 0, 0, 0]))

    def test_extended_header_goes_when_nothing_needs_it(self):
        tags = Image.Exif()
        tags[0x010F] = MARKER.decode()
        data = encode(gradient(), "WEBP", lossless=True, exif=tags.tobytes())
        self.assertEqual(webp.chunks(data)[0][0], b"VP8X")
        self.assertEqual([kind for kind, _ in webp.chunks(self.assertRebuilt(data))], [b"VP8L"])

    def test_animation_and_alpha_are_kept(self):
        frames = [gradient("RGBA", shift=n * 40) for n in range(3)]
        data = encode(frames[0], "WEBP", save_all=True, append_images=frames[1:], duration=[50, 90, 130],
                      loop=2, quality=80)
        parts = webp.chunks(data)
        # Hide a private chunk inside the first frame and set its reserved flag bits.
        index = next(n for n, (kind, _) in enumerate(parts) if kind == b"ANMF")
        frame = parts[index][1]
        parts[index] = (b"ANMF", frame[:15] + bytes([frame[15] | 0xF0]) + frame[16:] + webp.chunk(b"SECR", MARKER))
        rebuilt = self.assertRebuilt(riff(parts))
        first = next(payload for kind, payload in webp.chunks(rebuilt) if kind == b"ANMF")
        self.assertEqual(first[15] & 0xFC, 0)
        self.assertEqual(decoded(rebuilt)[0], 2)

    def test_damaged_files_are_refused(self):
        data = encode(gradient(), "WEBP", quality=80, exif=private_exif())
        wrong_size = data[:4] + struct.pack("<I", len(data)) + data[8:]
        no_image = riff([(b"VP8X", bytes(10)), (b"EXIF", private_exif())])
        short_header = riff([(b"VP8X", bytes(9))] + webp.chunks(data)[1:])
        for damaged in (data[:30], wrong_size, no_image, short_header, b"RIFF\0\0\0\0WEBP"):
            with self.subTest(size=len(damaged)), self.assertRaises(FormatError) as caught:
                webp.rebuild(damaged)
            self.assertEqual(caught.exception.key, "damaged")

    def test_verify_notices_a_tampered_result(self):
        data = encode(gradient("RGBA"), "WEBP", quality=80, icc_profile=PROFILE)
        rebuilt = webp.rebuild(data)
        parts = webp.chunks(rebuilt)
        for result in (riff(parts + [(b"XMP ", MARKER)]), rebuilt + MARKER, riff(parts[:1] + parts[2:])):
            with self.subTest(size=len(result)), self.assertRaises(VerificationError):
                webp.verify(data, result)


def extension(label, *pieces):
    return gif.extension(label, list(pieces))


def with_blocks(data, *blocks, trailing=b""):
    """Insert blocks after the header and color table; add bytes after the trailer."""
    header = next(block for kind, block in gif.blocks(data) if kind == "header")
    return header + b"".join(blocks) + data[len(header):] + trailing


class GifTests(unittest.TestCase):
    def assertRebuilt(self, data):
        rebuilt = gif.rebuild(data)
        gif.verify(data, rebuilt)
        self.assertEqual(decoded(rebuilt), decoded(data))
        self.assertNotIn(MARKER, rebuilt)
        return rebuilt

    def test_comments_text_and_private_extensions_are_dropped(self):
        data = encode(gradient("P"), "GIF", comment=MARKER)
        data = with_blocks(data, extension(0xFF, b"XMP DataXMP", MARKER),
                           extension(0xFF, b"SECRETAP1.0", MARKER),
                           extension(0x01, bytes(12), MARKER), trailing=MARKER)
        rebuilt = self.assertRebuilt(data)
        self.assertEqual([kind for kind, _ in gif.blocks(rebuilt)], ["header", "image", "trailer"])

    def test_animation_timing_and_transparency_are_kept(self):
        frames = [gradient("P", shift=n * 50) for n in range(3)]
        data = encode(frames[0], "GIF", save_all=True, append_images=frames[1:], duration=[40, 70, 100],
                      loop=2, transparency=0, comment=MARKER)
        rebuilt = self.assertRebuilt(data)
        loops = [block for kind, block in gif.blocks(rebuilt) if kind == 0xFF]
        self.assertEqual(loops, [b"\x21\xff\x0bNETSCAPE2.0\x03\x01\x02\x00\x00"])
        self.assertEqual(sum(kind == 0xF9 for kind, _ in gif.blocks(rebuilt)), 3)

    def test_reserved_control_bits_are_cleared(self):
        data = encode(gradient("P"), "GIF", transparency=0)
        control = next(block for kind, block in gif.blocks(data) if kind == 0xF9)
        tampered = data.replace(control, control[:3] + bytes([control[3] | 0xE0]) + control[4:])
        rebuilt = self.assertRebuilt(tampered)
        self.assertEqual(next(block for kind, block in gif.blocks(rebuilt) if kind == 0xF9), control)

    def test_profile_is_sanitized(self):
        pieces = [PROFILE[n:n + 200] for n in range(0, len(PROFILE), 200)]
        rebuilt = self.assertRebuilt(with_blocks(encode(gradient("P"), "GIF"),
                                                 extension(0xFF, gif.ICC_APPLICATION, *pieces)))
        block = next(block for kind, block in gif.blocks(rebuilt) if kind == 0xFF)
        self.assertEqual(b"".join(gif.sub_blocks(block, 2)[1:]), icc.sanitize(PROFILE))

    def test_missing_trailer_is_supplied(self):
        data = encode(gradient("P"), "GIF")
        self.assertEqual(self.assertRebuilt(data[:-1]), data)

    def test_damaged_files_are_refused(self):
        data = encode(gradient("P"), "GIF", transparency=0)
        control = next(block for kind, block in gif.blocks(data) if kind == 0xF9)
        for damaged in (data[:-12], data[:-1] + b"\x99\x3b", data.replace(control, control[:2] + b"\x05" + control[3:7] + b"\0\0"),
                        b"GIF89a"):
            with self.subTest(size=len(damaged)), self.assertRaises(FormatError) as caught:
                gif.rebuild(damaged)
            self.assertEqual(caught.exception.key, "damaged")

    def test_verify_notices_a_tampered_result(self):
        data = encode(gradient("P"), "GIF")
        rebuilt = gif.rebuild(data)
        for result in (rebuilt + MARKER, with_blocks(rebuilt, extension(0xFE, b"x")), rebuilt[:-1]):
            with self.subTest(size=len(result)), self.assertRaises(VerificationError):
                gif.verify(data, result)


if __name__ == "__main__":
    unittest.main()
