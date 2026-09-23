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


if __name__ == "__main__":
    unittest.main()
