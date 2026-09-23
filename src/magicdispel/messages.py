"""Everything the command line says."""

TEXT = {
    "usage": """\
Type magicdispel and a space, drag photos into the terminal, and press Enter.
Run magicdispel --help to see all options.""",
    "help": """\
magicdispel {version}: remove private metadata from photos, keeping image quality

Usage: type magicdispel and a space, drag photos into the terminal, press Enter.
       magicdispel photo.jpg screenshot.png ...

A cleaned copy is saved next to each original, named photo_clean.jpg, without
the dates and times the original's name may contain (as in screenshots).
Originals are never modified and existing files are never overwritten.

Options:
  --anonymous   name outputs photo_<random>.jpg instead of after the original
                (the picture itself is not changed)
  --keep-name   keep the original's name as it is, dates and times included
  --check       check that everything needed is installed
  --version     show the version
  -h, --help    show this help

Removes: location, capture time, camera and lens details, author, comments,
         editing software, and HEIC depth maps, portrait mattes, thumbnails.
Keeps:   image quality (no recompression), orientation, color, HDR,
         transparency and animation.
Formats: JPG, PNG, HEIC, AVIF, WebP, GIF, TIFF, BMP (BMP becomes lossless PNG).

This is not an anonymity tool: what the picture shows can still identify
people and places.""",
    "cleaned": "Cleaned: {path}",
    "failed": "Failed: {photo}\n  {reason}",
    "summary": "Done: {succeeded} succeeded, {failed} failed.",
    "ready": "Ready: magicdispel {version}",
    "second_check_on": "ExifTool second check: on (ExifTool {exiftool_version}, {exiftool})",
    "second_check_off": "ExifTool second check: off (ExifTool is optional and not installed)",
    "second_check_old": "ExifTool second check: off (ExifTool {exiftool_version} is older than 12.73)",
    "second_check_unusable": "ExifTool second check: off ({exiftool} does not run)",
    "exiftool_misconfigured": "MAGICDISPEL_EXIFTOOL does not name a program: {path}",
    "exiftool_problem": "ExifTool's second check found a problem ({detail}); nothing was saved.",
    "error": "Error: {reason}",
    "bad_arguments": "Invalid arguments: {detail}\nRun magicdispel --help for usage.",
    "cancelled": "\nCancelled.",
    # Why a file was not cleaned. Nothing is saved in any of these cases.
    "not_a_file": "Not a readable file: {path}",
    "unsupported_format": "This file type is not supported. Supported: JPG, PNG, HEIC, AVIF, WebP, GIF, TIFF "
                          "and BMP.",
    "damaged": "This {format} file is damaged or incomplete.",
    "unsupported_variant": "This kind of {format} file is not supported.",
    "unsupported_part": "This {format} file contains data that cannot be handled safely ({part}).",
    "unsupported_profile": "The color profile in this {format} file cannot be cleaned safely ({detail}).",
    "undecodable": "This {format} file cannot be decoded to check the result, so it was not cleaned.",
    "raw_photo": "RAW photos (DNG, CR2, NEF, ARW and others) are not supported. Export a JPEG or HEIC copy "
                 "first.",
    "too_large": "This {format} image is too large to check safely (the limit is {limit} megapixels).",
    "extra_image_data": "The image data in this {format} file carries extra hidden bytes.",
    "verification_failed": "The cleaned copy did not pass verification ({detail}); nothing was saved.",
    "pixels_changed": "The cleaned copy would not look identical to the original; nothing was saved.",
    "metadata_remains": "Metadata remained after cleaning ({tags}); nothing was saved.",
    "unexpected_error": "Unexpected error ({detail}); nothing was saved.",
    "source_changed": "Another program changed the original while it was being cleaned; nothing was saved. "
                      "Please try again.",
}


def message(key, **values):
    return TEXT[key].format(**values)
