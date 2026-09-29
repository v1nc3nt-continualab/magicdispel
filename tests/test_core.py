"""The cleaning pipeline: what has to pass before anything is saved."""
import io
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from magicdispel import core, formats
from magicdispel.errors import FormatError, InputError, VerificationError
from magicdispel.formats import png


class PipelineTests(unittest.TestCase):
    def test_a_result_its_format_cannot_read_back_fails_verification(self):
        with tempfile.TemporaryDirectory(prefix="core-") as folder:
            photo = Path(folder, "photo.png")
            Image.new("RGB", (8, 8), "green").save(photo)
            with patch.object(png, "verify", side_effect=FormatError("damaged", format="PNG")), \
                    self.assertRaises(VerificationError) as caught:
                core.clean(str(photo))
            self.assertEqual(caught.exception.key, "verification_failed")
            self.assertEqual(sorted(path.name for path in Path(folder).iterdir()), ["photo.png"])

    def test_a_file_of_another_kind_is_refused_without_reading_it(self):
        # A large file that is not a photo must not be read into memory to be turned away.
        with tempfile.TemporaryDirectory(prefix="core-") as folder:
            for name, head in (("paper.pdf", b"%PDF-1.7\n"), ("archive.zip", b"PK\x03\x04"), ("empty.jpg", b"")):
                path = Path(folder, name)
                path.write_bytes(head + bytes(1 << 20))
                with self.subTest(name), patch.object(Path, "read_bytes", side_effect=AssertionError("read")), \
                        self.assertRaises(InputError) as caught:
                    core.clean(str(path))
                self.assertEqual(caught.exception.key, "unsupported_format")

    def test_the_first_bytes_of_every_format_are_recognized(self):
        photo = Image.new("RGB", (8, 8), "green")
        for format in ("PNG", "JPEG", "GIF", "WEBP", "TIFF", "BMP"):
            buffer = io.BytesIO()
            photo.save(buffer, format)
            with self.subTest(format=format):
                self.assertTrue(formats.recognized(buffer.getvalue()[:core.HEAD]))
                self.assertIsNotNone(formats.identify(buffer.getvalue()))
        for head in (b"\0\0\0\x18ftypheic", b"\0\0\0\x14ftypqt  ", b"\0\0\0\x08wide", b"\xff\xd8\xff", b"II+\0"):
            with self.subTest(head=head):
                self.assertTrue(formats.recognized(head))

    def test_image_sequences_keep_their_extensions(self):
        for name, kind in (("burst.heics", "HEIC"), ("burst.heifs", "HEIC"), ("loop.avifs", "AVIF"),
                           ("photo.HEIC", "HEIC"), ("photo.jpg", "HEIC"), ("photo.avif", "AVIF")):
            with self.subTest(name):
                expected = Path(name).suffix if name != "photo.jpg" else ".heic"
                self.assertEqual(core.output_suffix(Path(name), kind), expected)

    def test_nothing_is_saved_when_a_file_cannot_be_cleaned(self):
        with tempfile.TemporaryDirectory(prefix="core-") as folder:
            photo = Path(folder, "photo.jpg")
            Image.new("RGB", (8, 8), "green").save(photo)
            photo.write_bytes(photo.read_bytes()[:-40])
            with self.assertRaises(FormatError):
                core.clean(str(photo))
            self.assertEqual(sorted(path.name for path in Path(folder).iterdir()), ["photo.jpg"])

    def test_a_photo_gets_its_name_only_when_it_is_whole(self):
        with tempfile.TemporaryDirectory(prefix="core-") as folder:
            photo = Path(folder, "photo.png")
            Image.new("RGB", (8, 8), "green").save(photo)

            def stopped(partial, *arguments):
                # Where a crash or a full disk would leave things: the bytes under a name that says so.
                self.assertEqual(sorted(path.name for path in Path(folder).iterdir() if path != photo),
                                 [partial.name])
                self.assertIn(".unfinished", partial.name)
                self.assertGreater(partial.stat().st_size, 0)
                raise OSError("stopped")

            with patch.object(core, "rename", side_effect=stopped), self.assertRaises(OSError):
                core.clean(str(photo))
            self.assertEqual(sorted(path.name for path in Path(folder).iterdir()), ["photo.png"])

    def test_an_existing_file_is_never_replaced(self):
        with tempfile.TemporaryDirectory(prefix="core-") as folder:
            photo = Path(folder, "photo.png")
            Image.new("RGB", (8, 8), "green").save(photo)
            Path(folder, "photo_clean.png").write_bytes(b"KEEP")
            self.assertEqual(core.clean(str(photo)).name, "photo_clean_1.png")
            self.assertEqual(core.clean(str(photo)).name, "photo_clean_2.png")
            self.assertEqual(Path(folder, "photo_clean.png").read_bytes(), b"KEEP")

    @unittest.skipIf(os.name == "nt", "Windows only has a read-only flag")
    def test_a_copy_gets_the_originals_permissions_even_when_they_forbid_writing(self):
        # Clearing the extended attributes macOS gives every new file needs the right to write to it.
        with tempfile.TemporaryDirectory(prefix="core-") as folder:
            for mode in (0o444, 0o400, 0o640):
                photo = Path(folder, "photo%o.png" % mode)
                Image.new("RGB", (8, 8), "green").save(photo)
                photo.chmod(mode)
                with self.subTest(mode=oct(mode)):
                    self.assertEqual(stat.S_IMODE(core.clean(str(photo)).stat().st_mode), mode)
                photo.chmod(0o600)


if __name__ == "__main__":
    unittest.main()
