"""Command line: type magicdispel, drag photos into the terminal, press Enter."""

import argparse
import sys

from . import __version__, exiftool
from .core import clean
from .messages import message


class Parser(argparse.ArgumentParser):
    """Report bad arguments in the user's language; the help text is our own."""

    def error(self, detail):
        print(message("bad_arguments", detail=detail), file=sys.stderr)
        sys.exit(2)


def main(argv=None):
    parser = Parser(prog="magicdispel", add_help=False)
    parser.add_argument("-h", "--help", action="store_true")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--anonymous", action="store_true")
    parser.add_argument("photos", nargs="*")
    args = parser.parse_args(argv)
    if args.version:
        print("magicdispel " + __version__)
        return 0
    if args.help or not (args.photos or args.check):
        print(message("help", version=__version__))
        return 0
    try:
        # ExifTool is optional: when present and recent, it double-checks results.
        path = exiftool.find()
        found = exiftool.version(path) if path else None
        second_check = path if exiftool.usable(found) else None
        if args.check:
            print(message("ready", version=__version__))
            print(message(second_check_state(path, found, second_check), exiftool_version=found, exiftool=path))
        failures = 0
        for photo in args.photos:
            try:
                print(message("cleaned", path=clean(photo, second_check, anonymous=args.anonymous)))
            except (OSError, ValueError) as error:
                failures += 1
                print(message("failed", photo=photo, reason=error), file=sys.stderr)
        if len(args.photos) > 1:
            print(message("summary", succeeded=len(args.photos) - failures, failed=failures))
        return 1 if failures else 0
    except (OSError, ValueError) as error:
        print(message("error", reason=error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(message("cancelled"), file=sys.stderr)
        return 130


def second_check_state(path, found_version, second_check):
    """Which --check line describes the optional ExifTool second check."""
    if second_check:
        return "second_check_on"
    if found_version:
        return "second_check_old"
    return "second_check_unusable" if path else "second_check_off"


if __name__ == "__main__":
    sys.exit(main())
