"""The ExifTool second opinion: which tags a cleaned file may still have."""
import unittest

from magicdispel import exiftool
from magicdispel.errors import VerificationError


class CheckTagsTests(unittest.TestCase):
    def test_hdr_rendering_fields_may_remain(self):
        exiftool.check_tags({"XMP:XMP-HDRGainMap:HDRGainMapHeadroom": 2.5,
                             "MakerNotes:Apple:HDRHeadroom": 1.2, "EXIF:IFD0:Orientation": 6}, "HEIC")

    def test_depth_data_and_toolkit_names_may_not(self):
        for group, tag, value in (("XMP-depthData", "IntrinsicMatrix", [1] * 9),
                                  ("XMP-depthBlurEffect", "RenderingParameters", "UkVORA=="),
                                  ("XMP-x", "XMPToolkit", "XMP Core 6.0.0")):
            with self.subTest(tag=tag), self.assertRaises(VerificationError):
                exiftool.check_tags({"XMP:" + group + ":" + tag: value}, "HEIC")


if __name__ == "__main__":
    unittest.main()
