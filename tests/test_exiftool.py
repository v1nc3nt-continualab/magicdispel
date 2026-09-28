"""The ExifTool second opinion: which tags a cleaned file may still have."""
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from magicdispel import exiftool
from magicdispel.errors import UserError, VerificationError


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


class FindTests(unittest.TestCase):
    """Where ExifTool is looked for: the folders of PATH given in full, never the current directory."""

    def program(self, folder, name="exiftool"):
        path = Path(folder, name + (".exe" if sys.platform == "win32" else ""))
        path.write_bytes(b"#!/bin/sh\n")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return str(path)

    def test_a_program_in_the_current_directory_is_never_run(self):
        with tempfile.TemporaryDirectory(prefix="find-") as planted, \
                tempfile.TemporaryDirectory(prefix="path-") as real:
            self.program(planted)
            for path in ("", os.pathsep, os.pathsep.join([".", "relative", ""]), "relative"):
                with self.subTest(path=path), patch.dict(os.environ, {"PATH": path}, clear=False):
                    previous = os.getcwd()
                    os.chdir(planted)
                    try:
                        self.assertIsNone(exiftool.which("exiftool"))
                        with patch.dict(os.environ, {"MAGICDISPEL_EXIFTOOL": "exiftool"}):
                            with self.assertRaises(UserError):
                                exiftool.find()
                    finally:
                        os.chdir(previous)
            found = self.program(real)
            with patch.dict(os.environ, {"PATH": os.pathsep.join(["", ".", real])}, clear=False):
                os.chdir(planted)
                try:
                    self.assertEqual(exiftool.which("exiftool"), found)
                finally:
                    os.chdir(previous)

    def test_an_explicit_program_is_found_by_its_name_or_its_path(self):
        with tempfile.TemporaryDirectory(prefix="find-") as folder:
            found = self.program(folder, "my-exiftool")
            with patch.dict(os.environ, {"PATH": folder, "MAGICDISPEL_EXIFTOOL": "my-exiftool"}):
                self.assertEqual(exiftool.find(), found)
            with patch.dict(os.environ, {"MAGICDISPEL_EXIFTOOL": found}):
                self.assertEqual(exiftool.find(), found)
            with patch.dict(os.environ, {"MAGICDISPEL_EXIFTOOL": os.path.join(folder, "missing")}), \
                    self.assertRaises(UserError):
                exiftool.find()


if __name__ == "__main__":
    unittest.main()
