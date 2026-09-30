"""The ExifTool second opinion: which tags a cleaned file may still have."""
import os
import stat
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

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


class BigMovieTests(unittest.TestCase):
    """ExifTool skips a movie box of more than 32 MiB unless told to ignore minor problems."""

    def movie(self, *boxes):
        """A file holding only these boxes' headers: the size of a box is all that is read."""
        folder = tempfile.TemporaryDirectory(prefix="exiftool-")
        self.addCleanup(folder.cleanup)
        path = Path(folder.name, "long.mov")
        path.write_bytes(b"".join(boxes))
        return path

    def test_a_long_recordings_movie_box_is_found_by_its_size_alone(self):
        big = 33 << 20
        ftyp = struct.pack(">I4s", 20, b"ftyp") + bytes(12)
        cases = (
            ("a movie box of 33 MiB", [ftyp, struct.pack(">I4s", big, b"moov")], True),
            ("a 64-bit size", [struct.pack(">I4sQ", 1, b"moov", big)], True),
            ("a small movie box", [ftyp, struct.pack(">I4s", 100, b"moov") + bytes(92)], False),
            ("a big media box", [struct.pack(">I4s", big, b"mdat")], False),
            ("no box at all", [bytes(4)], False),
            ("a box of no size", [struct.pack(">I4s", 4, b"free") + bytes(8)], False),
        )
        for name, boxes, expected in cases:
            with self.subTest(name):
                self.assertEqual(exiftool.big_movie(self.movie(*boxes)), expected)

    def test_exiftool_is_told_to_ignore_minor_problems_only_for_such_a_video(self):
        for size, flag in ((40 << 20, True), (8, False)):
            path = self.movie(struct.pack(">I4s", size, b"moov"))
            with patch.object(exiftool, "run", return_value=Mock(stdout=b"[{}]")) as run:
                exiftool.read("exiftool", None, path)
            self.assertEqual("-m" in run.call_args.args[1], flag)


class FindTests(unittest.TestCase):
    """Where ExifTool is looked for: the folders of PATH given in full, never the current directory."""

    def program(self, folder, name="exiftool", extension=".exe" if sys.platform == "win32" else ""):
        path = Path(folder, name + extension)
        path.write_bytes(b"#!/bin/sh\n")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return str(path)

    def assertSamePath(self, found, expected):
        """The same file: Windows writes the extension as PATHEXT does (.EXE) and ignores case."""
        self.assertEqual(os.path.normcase(found), os.path.normcase(expected))

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
                    self.assertSamePath(exiftool.which("exiftool"), found)
                finally:
                    os.chdir(previous)

    def test_an_explicit_program_is_found_by_its_name_or_its_path(self):
        with tempfile.TemporaryDirectory(prefix="find-") as folder:
            found = self.program(folder, "my-exiftool")
            with patch.dict(os.environ, {"PATH": folder, "MAGICDISPEL_EXIFTOOL": "my-exiftool"}):
                self.assertSamePath(exiftool.find(), found)
            with patch.dict(os.environ, {"MAGICDISPEL_EXIFTOOL": found}):
                self.assertSamePath(exiftool.find(), found)
            with patch.dict(os.environ, {"MAGICDISPEL_EXIFTOOL": os.path.join(folder, "missing")}), \
                    self.assertRaises(UserError):
                exiftool.find()

    def test_a_name_with_an_extension_of_pathext_is_looked_for_as_it_is(self):
        # MAGICDISPEL_EXIFTOOL=exiftool.exe on Windows is exiftool.exe, not exiftool.exe.EXE.
        with tempfile.TemporaryDirectory(prefix="find-") as folder:
            found = self.program(folder, extension=".exe")
            windows = {"PATH": folder, "PATHEXT": os.pathsep.join([".COM", ".EXE"]),
                       "MAGICDISPEL_EXIFTOOL": "exiftool.exe"}
            with patch.dict(os.environ, windows), patch.object(sys, "platform", "win32"):
                self.assertSamePath(exiftool.find(), found)


if __name__ == "__main__":
    unittest.main()
