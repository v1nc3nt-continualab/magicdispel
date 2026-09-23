"""The logo shown when magicdispel runs on its own."""
import io
import os
import unittest
from unittest.mock import patch

from magicdispel import banner


class Terminal(io.StringIO):
    def isatty(self):
        return True


def welcome(columns, **environment):
    with patch.dict(os.environ, environment), \
            patch("shutil.get_terminal_size", return_value=os.terminal_size((columns, 24))):
        return banner.welcome("1.2.3", "Drag photos here.", Terminal())


class BannerTests(unittest.TestCase):
    def test_every_letter_row_has_the_letters_width(self):
        for letter, rows in banner.LETTERS.items():
            with self.subTest(letter=letter):
                self.assertEqual(len(rows), 6)
                self.assertEqual(len({len(row) for row in rows}), 1)

    def test_the_logo_takes_one_line_when_it_fits_and_two_when_not(self):
        wide, narrow = welcome(100, NO_COLOR="1"), welcome(80, NO_COLOR="1")
        self.assertIn(banner.logo(["MAGICDISPEL"])[0], wide)
        self.assertIn(banner.logo(["MAGIC", "DISPEL"])[6], narrow)
        self.assertTrue(all(len(line) <= 80 for line in narrow.splitlines()))
        for text in (wide, narrow):
            self.assertIn(banner.CREDITS, text)
            self.assertIn(banner.TAGLINE, text)
            self.assertIn("v1.2.3", text)
            self.assertIn("Drag photos here.", text)

    def test_color_in_a_terminal_unless_no_color_is_set(self):
        with patch.dict(os.environ, {"TERM": "xterm-256color"}):
            os.environ.pop("NO_COLOR", None)
            self.assertIn("\x1b[38;5;201m", welcome(100))
        self.assertNotIn("\x1b[", welcome(100, NO_COLOR="1"))

    def test_plain_text_outside_a_terminal(self):
        text = banner.welcome("1.2.3", "Drag photos here.", io.StringIO())
        self.assertNotIn("█", text)
        self.assertTrue(text.startswith("MagicDispel 1.2.3"))


if __name__ == "__main__":
    unittest.main()
