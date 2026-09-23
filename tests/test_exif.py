"""Reading and writing only the EXIF fields that affect display."""
import unittest

from PIL import Image

from magicdispel import exif

MARKER = "MD_EXIF_PRIVATE"


def pillow_exif(**tags):
    """EXIF written by Pillow (little-endian), with private fields around the display ones."""
    data = Image.Exif()
    data[0x010F] = MARKER                       # Make
    data[0x0131] = "Editor " + MARKER            # Software
    for tag, value in tags.items():
        data[int(tag, 16)] = value
    return data.tobytes()


class ExifTests(unittest.TestCase):
    def test_round_trip_of_every_display_field(self):
        fields = exif.DisplayFields(orientation=8, resolution=((300, 1), (300, 1), 2),
                                    color_space=0xFFFF, interop_index=b"R03")
        data = exif.build(fields)
        self.assertEqual(exif.display_fields(data), fields)
        self.assertEqual(exif.display_fields(b"Exif\0\0" + data), fields)

    def test_only_display_fields_are_read(self):
        data = pillow_exif(**{"0x0112": 6, "0x011A": 144.0, "0x011B": 144.0, "0x0128": 2})
        fields = exif.display_fields(data)
        self.assertEqual(fields.orientation, 6)
        self.assertEqual(fields.resolution[2], 2)
        self.assertEqual(fields.resolution[0][0] / fields.resolution[0][1], 144)
        self.assertNotIn(MARKER.encode(), exif.build(fields))

    def test_empty_and_unreadable_data(self):
        self.assertEqual(exif.build(exif.DisplayFields()), b"")
        for data in (b"", b"garbage", b"MM\0*\0\0\0\x08\0\x05", b"II*\0\xff\xff\xff\xff",
                     exif.build(exif.DisplayFields(orientation=3))[:-4]):
            with self.subTest(data=data[:12]):
                self.assertIsInstance(exif.display_fields(data), exif.DisplayFields)

    def test_invalid_values_read_as_absent(self):
        self.assertIsNone(exif.display_fields(pillow_exif(**{"0x0112": 9})).orientation)


if __name__ == "__main__":
    unittest.main()
