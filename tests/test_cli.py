"""Command-line behavior in English and Chinese; no ExifTool needed for most cases."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from magicdispel import __version__, messages


def run(*arguments, language="en"):
    environment = dict(os.environ, MAGICDISPEL_LANG=language)
    return subprocess.run([sys.executable, "-m", "magicdispel", *arguments],
                          capture_output=True, text=True, env=environment)


class CommandLineTests(unittest.TestCase):
    def test_help_in_both_languages(self):
        for language, phrase in (("en", "drag photos into the terminal"), ("zh", "把照片拖进终端")):
            for arguments in ((), ("--help",), ("-h",)):
                with self.subTest(language=language, arguments=arguments):
                    result = run(*arguments, language=language)
                    self.assertEqual(result.returncode, 0)
                    self.assertIn(phrase, result.stdout)
                    self.assertIn(__version__, result.stdout)

    def test_version(self):
        result = run("--version")
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "magicdispel " + __version__))

    def test_invalid_option_is_reported_in_the_users_language(self):
        result = run("--no-such-option", language="zh")
        self.assertEqual(result.returncode, 2)
        self.assertIn("参数有误", result.stderr)
        self.assertIn("--no-such-option", result.stderr)

    def test_batch_continues_after_a_failure(self):
        with tempfile.TemporaryDirectory(prefix="cli-") as folder:
            photo = Path(folder, "photo one.png")
            Image.new("RGB", (8, 8), "green").save(photo)
            result = run(str(photo), str(Path(folder, "missing.jpg")))
            self.assertEqual(result.returncode, 1)
            self.assertIn("Cleaned: " + str(Path(folder, "photo one_clean.png")), result.stdout)
            self.assertIn("Failed: " + str(Path(folder, "missing.jpg")), result.stderr)
            self.assertIn("Done: 1 succeeded, 1 failed.", result.stdout)
            result = run(str(Path(folder, "missing.jpg")), language="zh")
            self.assertIn("失败：", result.stderr)


class LanguageTests(unittest.TestCase):
    def detect(self, environment, system=""):
        with patch.dict(os.environ, environment, clear=True), \
                patch.object(messages, "system_language", return_value=system):
            return messages.language()

    def test_explicit_setting_wins(self):
        self.assertEqual(self.detect({"MAGICDISPEL_LANG": "en", "LANG": "zh_CN.UTF-8"}), "en")
        self.assertEqual(self.detect({"MAGICDISPEL_LANG": "ZH", "LANG": "en_US.UTF-8"}), "zh")

    def test_locale_variables_in_posix_order(self):
        self.assertEqual(self.detect({"LANG": "zh_CN.UTF-8"}), "zh")
        self.assertEqual(self.detect({"LC_ALL": "en_US.UTF-8", "LANG": "zh_CN.UTF-8"}), "en")
        self.assertEqual(self.detect({"LANG": "C.UTF-8"}, system="zh-Hans-CN"), "en")

    def test_system_language_when_no_locale_is_set(self):
        self.assertEqual(self.detect({}, system="zh-Hans-CN"), "zh")
        self.assertEqual(self.detect({}, system="en-US"), "en")
        self.assertEqual(self.detect({}, system=""), "en")


if __name__ == "__main__":
    unittest.main()
