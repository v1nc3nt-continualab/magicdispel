"""Command line: type magicdispel, drag photos into the terminal, press Enter."""

import argparse
import sys

from . import __version__, banner, exiftool
from .core import clean
from .errors import UserError
from .messages import message


class Parser(argparse.ArgumentParser):
    """Report bad arguments in our own words; the help text is our own too."""

    def error(self, detail):
        print(message("bad_arguments", detail=detail), file=sys.stderr)
        sys.exit(2)


def main(argv=None):
    parser = Parser(prog="magicdispel", add_help=False)
    parser.add_argument("-h", "--help", action="store_true")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--check", action="store_true")
    naming = parser.add_mutually_exclusive_group()
    naming.add_argument("--anonymous", dest="naming", action="store_const", const="anonymous", default="plain")
    naming.add_argument("--keep-name", dest="naming", action="store_const", const="original")
    parser.add_argument("photos", nargs="*")
    # A path the console cannot show is printed with replacements, not refused.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = parser.parse_args(argv)
    if args.version:
        print("magicdispel " + __version__)
        return 0
    if args.help:
        print(message("help", version=__version__))
        return 0
    if not (args.photos or args.check):
        print(banner.welcome(__version__, message("usage")))
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
                print(message("cleaned", path=clean(photo, second_check, args.naming)))
            except Exception as error:  # one photo failing never stops the others
                failures += 1
                print(message("failed", photo=photo, reason=explained(error)), file=sys.stderr)
        if len(args.photos) > 1:
            print(message("summary", succeeded=len(args.photos) - failures, failed=failures))
        return 1 if failures else 0
    except Exception as error:
        print(message("error", reason=explained(error)), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(message("cancelled"), file=sys.stderr)
        return 130


def explained(error):
    """Our own errors and system errors say what went wrong. Anything else is a
    bug, or an input no check anticipated; it is reported as unexpected."""
    if isinstance(error, (UserError, OSError)):
        return str(error)
    return message("unexpected_error", detail="%s: %s" % (type(error).__name__, error))


def second_check_state(path, found_version, second_check):
    """Which --check line describes the optional ExifTool second check."""
    if second_check:
        return "second_check_on"
    if found_version:
        return "second_check_old"
    return "second_check_unusable" if path else "second_check_off"


if __name__ == "__main__":
    sys.exit(main())
