"""The cleaning pipeline: what has to pass before anything is saved."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from magicdispel import core
from magicdispel.errors import FormatError, VerificationError
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

    def test_nothing_is_saved_when_a_file_cannot_be_cleaned(self):
        with tempfile.TemporaryDirectory(prefix="core-") as folder:
            photo = Path(folder, "photo.jpg")
            Image.new("RGB", (8, 8), "green").save(photo)
            photo.write_bytes(photo.read_bytes()[:-40])
            with self.assertRaises(FormatError):
                core.clean(str(photo))
            self.assertEqual(sorted(path.name for path in Path(folder).iterdir()), ["photo.jpg"])

    def test_an_existing_file_is_never_replaced(self):
        with tempfile.TemporaryDirectory(prefix="core-") as folder:
            photo = Path(folder, "photo.png")
            Image.new("RGB", (8, 8), "green").save(photo)
            Path(folder, "photo_clean.png").write_bytes(b"KEEP")
            self.assertEqual(core.clean(str(photo)).name, "photo_clean_1.png")
            self.assertEqual(core.clean(str(photo)).name, "photo_clean_2.png")
            self.assertEqual(Path(folder, "photo_clean.png").read_bytes(), b"KEEP")


if __name__ == "__main__":
    unittest.main()
