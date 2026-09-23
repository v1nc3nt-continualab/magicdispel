"""Command line: type magicdispel, drag photos into the terminal, press Enter."""

import argparse
import sys

from . import __version__
from .core import CleanError, clean, exiftool_version, find_exiftool, supported_exiftool
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
        exiftool, version = find_exiftool(), None
        if exiftool:
            version = exiftool_version(exiftool)
            if not supported_exiftool(version):
                exiftool = None
        if args.check:
            print(message("ready", version=__version__))
            if exiftool:
                print(message("second_check_on", exiftool_version=version, exiftool=exiftool))
            else:
                print(message("second_check_old" if version else "second_check_off", exiftool_version=version))
        failures = 0
        for photo in args.photos:
            try:
                print(message("cleaned", path=clean(exiftool, photo, anonymous=args.anonymous)))
            except (CleanError, OSError, ValueError) as error:
                failures += 1
                print(message("failed", photo=photo, reason=error), file=sys.stderr)
        if len(args.photos) > 1:
            print(message("summary", succeeded=len(args.photos) - failures, failed=failures))
        return 1 if failures else 0
    except (CleanError, OSError) as error:
        print(message("error", reason=error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(message("cancelled"), file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
