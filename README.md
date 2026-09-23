# MagicDispel

Remove private metadata from photos on your own computer, without touching image quality.

```console
magicdispel photo.jpg
magicdispel "photo one.heic" screenshot.png
magicdispel --anonymous photo.jpg
```

Type `magicdispel` and a space, drag one or more photos into the terminal, and press Enter.
A cleaned copy named `photo_clean.jpg` appears next to each original, without the dates and
times that screenshots and phone cameras put in names: `Screenshot 2026-09-23 at 15.14.15.png`
becomes `Screenshot_clean.png`. Originals are never modified and existing files are never
overwritten: further copies get `_clean_1`, `_clean_2`... `--keep-name` keeps the name as it is;
`--anonymous` names the copy `photo_<random>.jpg` instead. It changes the name only, not what
the picture shows.

## How it works

MagicDispel does not hunt for known metadata to delete. It writes a new file from only the
parts a viewer needs to show the image, such as the compressed pixels, color profile,
orientation, DPI, transparency, animation timing and HDR gain maps, and leaves everything
else behind: location, capture time, camera and lens details, author, comments, editing
software, thumbnails, depth maps and portrait mattes, C2PA manifests, and private or unknown
data blocks, wherever a format stores them.

- Image data is copied byte for byte. Only BMP is re-encoded, losslessly, as PNG.
- ICC color profiles keep their color data; their dates become a fixed placeholder
  (`2000-01-01`), their descriptions `Clean`, and device and creator fields are cleared.
- Every result is checked before it is saved. The format's rebuilder parses it again on its
  own; Pillow must decode identical pixels and frames (for HEIC, which Pillow cannot decode,
  every image item is compared byte for byte instead); and ExifTool, if installed, gives an
  independent second reading. If any check fails, nothing is saved.
- Everything happens on your computer: no uploads, telemetry or network access.

**This is not an anonymity tool.** What the picture shows, a match with a copy published
earlier, or the account it is shared from can still identify people and places. Removing
depth and style data also limits later portrait, depth-of-field and style edits.
See [privacy and format details](https://github.com/v1nc3nt-continualab/magicdispel/blob/main/docs/PRIVACY.md).

## Install

One command installs MagicDispel and everything it needs. It uses
[uv](https://docs.astral.sh/uv/), which brings its own Python when the system has none that fits,
and ends by showing the MagicDispel logo.

macOS and Linux:

```sh
curl -LsSf https://raw.githubusercontent.com/v1nc3nt-continualab/magicdispel/main/install.sh | sh
```

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/v1nc3nt-continualab/magicdispel/main/install.ps1 | iex"
```

If `magicdispel` is not found afterwards, open a new terminal window.

Already using uv or pipx (Python 3.10 or newer):

```sh
uv tool install magicdispel
pipx install magicdispel
```

To update, run the install command again, or `uv tool upgrade magicdispel`. To remove
MagicDispel, run `uv tool uninstall magicdispel`.

[ExifTool](https://exiftool.org/) is optional: when version 12.73 or newer is installed,
MagicDispel uses it to double-check every result. Install it with `brew install exiftool`,
`sudo apt install libimage-exiftool-perl` or `winget install --exact --id OliverBetz.ExifTool`.

## Usage

```sh
magicdispel --check
magicdispel --version
magicdispel photo.jpg screenshot.png portrait.heic
magicdispel --anonymous photo.jpg
magicdispel --keep-name "Screenshot 2026-09-23 at 15.14.15.png"
magicdispel -- "-filename-starts-with-a-dash.jpg"
```

Quote paths containing spaces, or drag the files into the terminal. On Windows, drag-and-drop
depends on the terminal; a quoted path always works. Folders are not processed recursively.
Run `magicdispel` on its own to see the logo and how to use it. If ExifTool is installed
somewhere unusual, set `MAGICDISPEL_EXIFTOOL` to its full path.

Exit codes: `0` success or help, `1` one or more files not cleaned, `2` invalid arguments,
`130` interrupted. A failed file does not stop the rest of a batch.

## Formats

| Format | Kept | Removed |
| --- | --- | --- |
| JPEG | image data, JFIF density, orientation, DPI, color space, ICC profile; HDR gain-map images (MPF), Apple HDR headroom and gain-map XMP | other EXIF and XMP, IPTC/Photoshop, comments, C2PA, thumbnails, maker notes, trailing data |
| PNG, APNG | image data, palette, transparency, color chunks (sRGB, gAMA, cHRM, cICP, HDR), DPI, animation, ICC profile, orientation | text, time stamps, C2PA, private chunks, anything after the end |
| HEIC, HEIF | image items and tiles, HDR gain maps (Apple and ISO), alpha, orientation, ICC profile, HDR XMP fields | EXIF, other XMP, Apple property lists, depth and calibration, portrait and semantic mattes, style maps, thumbnails, unused data |
| AVIF | as HEIF, including animations; sequence times, names and user data are cleared | as HEIF |
| WebP | image data, alpha, animation, ICC profile, orientation | other EXIF, XMP, unknown chunks |
| GIF | images, palettes, frame timing, transparency, loop count, ICC profile | comments, text overlays, XMP, other extensions |
| TIFF | image data, decoding tags, DPI, orientation, page numbers, ICC profile | EXIF and GPS directories, XMP, IPTC, Photoshop, descriptions, private tags, sub-images |
| BMP | converted to lossless PNG with the same pixels, DPI and profile | everything else |
| RAW, video, PDF | not supported; RAW files built on TIFF (DNG, CR2, NEF...) are recognized and refused | |

Variants that cannot be rebuilt safely, such as BigTIFF, fragmented image sequences or
unknown HEIF item types, are refused rather than passed through. Images up to 268 megapixels
are checked (enough for 200-megapixel phone photos); larger ones are refused.

## Development

```sh
python -m venv .venv
# Activate the environment for your shell.
python -m pip install -e ".[test]" build
python -m unittest discover -s tests -v
python -m build
```

The tests need only Pillow. When ExifTool is installed, they also add metadata the way other
programs write it and let ExifTool double-check each result; CI runs them both ways. No
personal photos are included.

Before and after any change to the cleaning code, run the regression harness over a folder
of real sample photos kept outside the repository:

```sh
python scripts/make_probes.py ~/magicdispel-corpus    # adds synthetic leak probes
python scripts/regression.py ~/magicdispel-corpus     # saves a run under runs/
python scripts/regression.py ~/magicdispel-corpus --baseline ~/magicdispel-corpus/runs/<run>.json
```

It checks that every output looks identical to its input (Pillow, and macOS ImageIO/ColorSync
when available), that no probe marker survives, and, against a baseline, that no sample
changes outcome or gains metadata. Pure refactors should also pass `--identical`, and
`--without-exiftool` checks the path users without ExifTool take.

`.github/workflows/test.yml` runs the tests on macOS, Windows and Linux with Python 3.10 and
3.13, with and without ExifTool, and the install scripts on all three. `release.yml` publishes a
tagged version to PyPI once those pass.

## License and attribution

MagicDispel is MIT licensed. [ExifTool](https://exiftool.org/), an optional companion, is
developed by Phil Harvey and distributed separately under its own license. MagicDispel is not
affiliated with ExifTool.
