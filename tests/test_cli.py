"""Command-line behavior in English and Chinese, with and without ExifTool."""
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from magicdispel import __version__, cli, core, exiftool, messages, names


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


class WithoutExifToolTests(unittest.TestCase):
    """ExifTool is optional: without it, results are still checked and saved."""

    def run_main(self, *arguments):
        with patch.dict(os.environ, {"MAGICDISPEL_LANG": "en"}), \
                patch.object(exiftool, "find", return_value=None), \
                patch("sys.stdout", new_callable=io.StringIO) as output:
            return cli.main(list(arguments)), output.getvalue()

    def test_check_reports_the_second_check_as_off(self):
        code, output = self.run_main("--check")
        self.assertEqual(code, 0)
        self.assertIn("ExifTool second check: off", output)

    def test_photos_are_cleaned(self):
        with tempfile.TemporaryDirectory(prefix="cli-") as folder:
            photo = Path(folder, "photo.jpg")
            Image.new("RGB", (8, 8), "blue").save(photo)
            code, output = self.run_main(str(photo))
            self.assertEqual(code, 0)
            self.assertTrue(Path(folder, "photo_clean.jpg").exists())

    def test_an_unexpected_error_fails_only_that_photo(self):
        with tempfile.TemporaryDirectory(prefix="cli-") as folder:
            first, second = Path(folder, "first.jpg"), Path(folder, "second.jpg")
            for photo in (first, second):
                Image.new("RGB", (8, 8), "blue").save(photo)

            def clean(photo, *arguments, **options):
                if photo == str(first):
                    raise RuntimeError("simulated bug")
                return core.clean(photo, *arguments, **options)

            with patch.object(cli, "clean", side_effect=clean), \
                    patch("sys.stderr", new_callable=io.StringIO) as errors:
                code, output = self.run_main(str(first), str(second))
            self.assertEqual(code, 1)
            self.assertIn("Unexpected error (RuntimeError: simulated bug)", errors.getvalue())
            self.assertIn("Done: 1 succeeded, 1 failed.", output)
            self.assertTrue(Path(folder, "second_clean.jpg").exists())


class AnonymousNameTests(unittest.TestCase):
    def test_outputs_get_random_names_that_never_replace_a_file(self):
        with tempfile.TemporaryDirectory(prefix="cli-") as temp:
            folder = Path(temp, "中文 空格")
            folder.mkdir()
            source = folder / "姓名_2026-09-23.JPG"
            Image.new("RGB", (24, 32), "blue").save(source)
            original = source.read_bytes()
            result = run("--anonymous", str(source))
            self.assertEqual(result.returncode, 0, result.stderr)
            outputs = list(folder.glob("photo_*.jpg"))
            self.assertEqual(len(outputs), 1)
            self.assertRegex(outputs[0].name, r"^photo_[0-9a-f]{32}\.jpg$")
            self.assertEqual(source.read_bytes(), original)
            taken = folder / ("photo_" + "a" * 32 + ".jpg")
            taken.write_bytes(b"KEEP_EXISTING")
            with patch.object(names.secrets, "token_hex", side_effect=["a" * 32, "b" * 32]):
                output = core.publish(outputs[0].read_bytes(), source, ".JPG", "anonymous")
            self.assertEqual(output.name, "photo_" + "b" * 32 + ".jpg")
            self.assertEqual(taken.read_bytes(), b"KEEP_EXISTING")
            bmp = folder / "姓名.bmp"
            Image.new("RGB", (8, 8), "red").save(bmp)
            self.assertRegex(core.clean(str(bmp), naming="anonymous").name, r"^photo_[0-9a-f]{32}\.png$")

    def test_dates_and_times_leave_the_name_unless_it_is_kept(self):
        with tempfile.TemporaryDirectory(prefix="cli-") as folder:
            screenshot = Path(folder, "截屏2026-09-23 下午3.14.15.png")
            Image.new("RGB", (8, 8), "blue").save(screenshot)
            self.assertEqual(run(str(screenshot)).returncode, 0)
            self.assertEqual(run("--keep-name", str(screenshot)).returncode, 0)
            self.assertEqual(sorted(path.name for path in Path(folder).glob("*_clean*")),
                             ["截屏2026-09-23 下午3.14.15_clean.png", "截屏_clean.png"])
            result = run("--keep-name", "--anonymous", str(screenshot), language="zh")
            self.assertEqual(result.returncode, 2)
            self.assertIn("参数有误", result.stderr)


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
