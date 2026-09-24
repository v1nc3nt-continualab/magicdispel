"""Command-line behavior, with and without ExifTool."""
import io
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from magicdispel import __version__, cli, core, exiftool, names


def run(*arguments):
    return subprocess.run([sys.executable, "-m", "magicdispel", *arguments], capture_output=True, text=True)


class CommandLineTests(unittest.TestCase):
    def test_welcome_and_help(self):
        for arguments in ((), ("--help",), ("-h",)):
            with self.subTest(arguments=arguments):
                result = run(*arguments)
                self.assertEqual(result.returncode, 0)
                self.assertIn("drag photos or videos into the terminal", result.stdout)
                self.assertIn(__version__, result.stdout)
        self.assertIn("Designed by VincentC", run().stdout)
        self.assertIn("--keep-name", run("--help").stdout)

    def test_version(self):
        result = run("--version")
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "magicdispel " + __version__))

    def test_invalid_option_is_reported(self):
        result = run("--no-such-option")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Invalid arguments", result.stderr)
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


    def test_names_and_messages_cannot_send_escape_sequences(self):
        result = run("missing \x1b[2J.jpg")
        self.assertNotIn("\x1b", result.stderr)
        self.assertIn("missing \\x1b[2J.jpg", result.stderr)

    @unittest.skipUnless(hasattr(signal, "SIGHUP"), "no hang-up signal")
    def test_a_run_that_ignores_hang_ups_keeps_ignoring_them(self):
        script = ("import signal; signal.signal(signal.SIGHUP, signal.SIG_IGN); from magicdispel import cli; "
                  "cli.main(['missing.jpg']); print(signal.getsignal(signal.SIGHUP) is signal.SIG_IGN)")
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
        self.assertEqual(result.stdout.split()[-1], "True")


class WithoutExifToolTests(unittest.TestCase):
    """ExifTool is optional: without it, results are still checked and saved."""

    def run_main(self, *arguments):
        with patch.object(exiftool, "find", return_value=None), \
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
            result = run("--keep-name", "--anonymous", str(screenshot))
            self.assertEqual(result.returncode, 2)
            self.assertIn("Invalid arguments", result.stderr)


if __name__ == "__main__":
    unittest.main()
