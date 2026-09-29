"""Comparing what Pillow decodes from a result and from its original."""
import io
import os
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from magicdispel import pixels
from magicdispel.errors import FormatError, VerificationError


def png(image):
    stream = io.BytesIO()
    image.save(stream, "PNG")
    return stream.getvalue()


class CompareTests(unittest.TestCase):
    def test_a_file_is_opened_only_by_the_decoder_of_its_kind(self):
        data = png(Image.new("RGB", (8, 8), "teal"))
        with pixels.opened(data, "PNG") as picture:
            self.assertEqual(picture.format, "PNG")
        for kind in sorted(pixels.FORMATS - {"PNG", "APNG"}):
            with self.subTest(kind=kind), self.assertRaises(OSError):  # Pillow's UnidentifiedImageError
                pixels.digest(data, kind)
        # A BMP's clean copy is a PNG, compared as one.
        stream = io.BytesIO()
        Image.new("RGB", (8, 8), "teal").save(stream, "BMP")
        pixels.compare(stream.getvalue(), data, "BMP")

    def test_frames_are_counted_against_the_pixels_a_file_may_ask_for(self):
        # A header says the canvas, and a frame count says how often it is decoded.
        frames = [Image.new("RGB", (10, 10), color) for color in ("red", "blue", "green")]
        stream = io.BytesIO()
        frames[0].save(stream, "GIF", save_all=True, append_images=frames[1:], duration=50)
        data = stream.getvalue()
        pixels.digest(data, "GIF")
        with patch.object(pixels, "FRAME_PIXELS", 299):  # three frames of a 10 by 10 canvas
            with self.assertRaises(FormatError) as caught:
                pixels.digest(data, "GIF")
            self.assertEqual(caught.exception.key, "too_many_pixels")
        with patch.object(pixels, "FRAME_PIXELS", 300):
            pixels.digest(data, "GIF")
        # Pages of a TIFF are counted as they are decoded, each at its own size.
        stream = io.BytesIO()
        frames[0].save(stream, "TIFF", save_all=True, append_images=frames[1:])
        with patch.object(pixels, "FRAME_PIXELS", 250), self.assertRaises(FormatError) as caught:
            pixels.digest(stream.getvalue(), "TIFF")
        self.assertEqual(caught.exception.key, "too_many_pixels")

    def test_a_webp_of_more_frames_than_libwebp_reads_in_good_time_is_refused(self):
        frames = [Image.new("RGB", (4, 4), color) for color in ("red", "blue", "green")]
        stream = io.BytesIO()
        frames[0].save(stream, "WEBP", save_all=True, append_images=frames[1:], duration=50, lossless=True)
        pixels.digest(stream.getvalue(), "WEBP")
        with patch.dict(pixels.MAX_FRAMES, {"WEBP": 2}), self.assertRaises(FormatError) as caught:
            pixels.digest(stream.getvalue(), "WEBP")
        self.assertEqual(caught.exception.key, "too_many_frames")

    def test_an_animation_is_checked_on_a_smaller_canvas_than_a_still_image(self):
        frames = [Image.new("RGB", (10, 10), color) for color in ("red", "blue")]
        for kind in ("GIF", "WEBP"):
            stream = io.BytesIO()
            frames[0].save(stream, kind, save_all=True, append_images=frames[1:], duration=50, lossless=True)
            animation = stream.getvalue()
            pixels.digest(animation, kind)
            with patch.object(pixels, "ANIMATION_MEGAPIXELS", 0.00005), self.subTest(kind), \
                    self.assertRaises(FormatError) as caught:  # 50 pixels, of a canvas of 100
                pixels.digest(animation, kind)
            self.assertEqual(caught.exception.key, "too_large")
        still = png(Image.new("RGB", (10, 10)))
        with patch.object(pixels, "ANIMATION_MEGAPIXELS", 0.00005):
            pixels.digest(still, "PNG")

    def test_what_libtiff_writes_to_standard_error_is_muted_while_pillow_decodes(self):
        with tempfile.TemporaryFile() as captured:
            saved = os.dup(2)
            try:
                os.dup2(captured.fileno(), 2)
                with pixels.quiet():
                    os.write(2, b"libtiff complains")
                    with self.assertRaises(ZeroDivisionError):  # and standard error is back, whatever happens
                        1 / 0
                os.write(2, b"the command says")
            finally:
                os.dup2(saved, 2)
                os.close(saved)
            captured.seek(0)
            self.assertEqual(captured.read(), b"the command says")

    def test_identical_pixels_pass(self):
        data = png(Image.new("RGB", (300, 600), "teal"))  # taller than one strip
        pixels.compare(data, data, "PNG")

    def test_a_changed_pixel_is_noticed(self):
        image = Image.new("RGB", (300, 600), "teal")
        before = png(image)
        image.putpixel((299, 599), (0, 0, 0))
        with self.assertRaises(VerificationError) as caught:
            pixels.compare(before, png(image), "PNG")
        self.assertEqual(caught.exception.key, "pixels_changed")

    def test_16_bit_samples_are_compared_at_full_precision(self):
        image = Image.new("I;16", (4, 4), 1000)
        before = png(image)
        image.putpixel((0, 0), 1001)  # indistinguishable in 8 bits
        with self.assertRaises(VerificationError):
            pixels.compare(before, png(image), "PNG")

    def test_an_undecodable_result_fails_verification(self):
        data = png(Image.new("RGB", (64, 64), "teal"))
        with self.assertRaises(VerificationError) as caught:
            pixels.compare(data, data[:60], "PNG")
        self.assertEqual(caught.exception.key, "verification_failed")

    def test_any_decoder_failure_on_the_original_is_reported_as_undecodable(self):
        data = png(Image.new("RGB", (8, 8)))
        with patch.object(pixels, "digest", side_effect=RuntimeError("decoder failed")), \
                self.assertRaises(FormatError) as caught:
            pixels.compare(data, data, "AVIF")
        self.assertEqual(caught.exception.key, "undecodable")

    def test_images_over_the_limit_are_refused(self):
        data = png(Image.new("RGB", (100, 100)))
        with patch.object(Image, "MAX_IMAGE_PIXELS", 1000), self.assertRaises(FormatError) as caught:
            pixels.compare(data, data, "PNG")
        self.assertEqual(caught.exception.key, "too_large")


if __name__ == "__main__":
    unittest.main()
