"""Portable command-line entry point."""

import argparse
import sys

from . import __version__
from .core import CleanError, check_dependency, clean, find_exiftool


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="magicdispel",
        description="Remove common photo metadata locally while preserving image quality.",
        epilog=("Type magicdispel and a space, drag in one or more photos, then press Enter.\n"
                "Outputs: photo_clean.jpg, photo_clean_1.jpg, ... next to the original.\n"
                "Formats: JPEG, PNG/APNG, HEIC/HEIF, AVIF, WebP, GIF, TIFF, BMP.\n"
                "BMP becomes lossless PNG; other formats keep their image encoding.\n"
                "Keeps color/orientation, HDR gain maps and transparency.\n"
                "Removes HEIF thumbnails, depth/calibration, masks and editing-only style maps.\n"
                "Sanitizes ICC identity fields; profile dates use a fixed 2000-01-01 placeholder.\n"
                "Later portrait/depth/style editing may be reduced.\n"
                "This is not a zero-metadata or anonymity tool."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version="%(prog)s " + __version__)
    parser.add_argument("--check", action="store_true", help="check the ExifTool dependency")
    parser.add_argument("--anonymous", action="store_true",
                        help="use a random output name without the original filename (does not anonymize image content)")
    parser.add_argument("photos", metavar="PHOTO", nargs="*")
    args = parser.parse_args(argv)
    if not args.photos and not args.check:
        parser.print_help()
        return 0
    try:
        exiftool = find_exiftool()
        version = check_dependency(exiftool)
        if args.check:
            print("Ready: magicdispel " + __version__ + " / ExifTool " + version)
            print("ExifTool: " + exiftool)
        failures = 0
        for argument in args.photos:
            try:
                destination = clean(exiftool, argument, anonymous=args.anonymous)
                print("Cleaned: " + str(destination))
            except (CleanError, OSError, ValueError) as error:
                failures += 1
                print("Failed: " + argument + "\n  " + str(error), file=sys.stderr)
        if len(args.photos) > 1:
            print("Done: {} succeeded, {} failed.".format(len(args.photos) - failures, failures))
        return 1 if failures else 0
    except (CleanError, OSError) as error:
        print("Error: " + str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
