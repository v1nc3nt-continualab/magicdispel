"""The ExifTool second opinion: which tags a cleaned file may still have."""
import unittest

from magicdispel import exiftool
from magicdispel.errors import VerificationError


class CheckTagsTests(unittest.TestCase):
    def test_hdr_rendering_fields_may_remain(self):
        exiftool.check_tags({"XMP:XMP-HDRGainMap:HDRGainMapHeadroom": 2.5,
                             "MakerNotes:Apple:HDRHeadroom": 1.2, "EXIF:IFD0:Orientation": 6}, "HEIC")

    def test_depth_data_toolkit_names_and_comments_may_not(self):
        for key, value, kind in (("XMP:XMP-depthData:IntrinsicMatrix", [1] * 9, "HEIC"),
                                 ("XMP:XMP-depthBlurEffect:RenderingParameters", "UkVORA==", "HEIC"),
                                 ("XMP:XMP-x:XMPToolkit", "XMP Core 6.0.0", "HEIC"),
                                 ("File:Comment", "PRIVATE_MARKER", "GIF")):
            with self.subTest(key=key), self.assertRaises(VerificationError):
                exiftool.check_tags({key: value}, kind)


if __name__ == "__main__":
    unittest.main()
