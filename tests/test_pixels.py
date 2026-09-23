"""Comparing what Pillow decodes from a result and from its original."""
import io
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

    def test_images_over_the_limit_are_refused(self):
        data = png(Image.new("RGB", (100, 100)))
        with patch.object(Image, "MAX_IMAGE_PIXELS", 1000), self.assertRaises(FormatError) as caught:
            pixels.compare(data, data, "PNG")
        self.assertEqual(caught.exception.key, "too_large")


if __name__ == "__main__":
    unittest.main()
