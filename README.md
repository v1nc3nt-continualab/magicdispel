<p align="center">
  <img src="https://raw.githubusercontent.com/v1nc3nt-continualab/magicdispel/main/docs/images/welcome.png" alt="MagicDispel: wipe the metadata, keep every pixel" width="820">
</p>

<p align="center">
  <b>Remove private metadata from photos on your own computer, without changing a single pixel.</b>
</p>

<p align="center">
  <a href="https://pypi.org/project/magicdispel/"><img alt="PyPI" src="https://img.shields.io/pypi/v/magicdispel?color=8a2be2"></a>
  <a href="https://github.com/v1nc3nt-continualab/magicdispel/actions/workflows/test.yml"><img alt="Tests" src="https://github.com/v1nc3nt-continualab/magicdispel/actions/workflows/test.yml/badge.svg"></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-3776ab">
  <img alt="macOS, Windows and Linux" src="https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-555">
  <a href="https://github.com/v1nc3nt-continualab/magicdispel/blob/main/LICENSE"><img alt="MIT license" src="https://img.shields.io/badge/license-MIT-2ea44f"></a>
</p>

A photo carries more than its pixels: where it was taken, when, on which phone, and sometimes
hidden extras such as depth maps, portrait mattes and even the serial number of your display.
MagicDispel saves a clean copy with none of that, and nothing a viewer shows is changed.

## Quick start

macOS and Linux:

```sh
curl -LsSf https://raw.githubusercontent.com/v1nc3nt-continualab/magicdispel/main/install.sh | sh
```

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/v1nc3nt-continualab/magicdispel/main/install.ps1 | iex"
```

Then type `magicdispel` and a space, drag photos into the terminal, and press Enter. A cleaned
copy appears next to each original.

<p align="center">
  <img src="https://raw.githubusercontent.com/v1nc3nt-continualab/magicdispel/main/docs/images/cleaning.png" alt="Before cleaning, ExifTool shows the photo's location, phone model and capture time. MagicDispel cleans two files and explains why it refuses a PDF. Afterwards, ExifTool finds none of those fields." width="820">
</p>

## Why MagicDispel

- **It rebuilds instead of deleting.** Most tools delete the metadata they know about.
  MagicDispel writes a new file from only the parts needed to show the image, so metadata it
  has never heard of is left behind too.
- **Every pixel stays.** Compressed image data is copied byte for byte, never recompressed.
  Color profiles, orientation, DPI, transparency, animation and HDR gain maps are kept, so a
  photo looks exactly the same, in HDR too.
- **It finds what others miss.** Depth maps and portrait mattes inside iPhone photos,
  thumbnails that may show the uncropped original, C2PA manifests, data hidden after the end
  of the image, the dates and times in file names, and the display model and serial number
  in screenshots' color profiles.
- **It checks before it saves.** Every result is read back on its own and compared with the
  original, frame by frame. ExifTool, if installed, gives an independent second opinion. If
  anything is off, nothing is saved.
- **It stays on your computer.** No uploads, telemetry or network access. Originals are never
  modified, and existing files are never overwritten.

## Compared with the usual one-liner

Many guides suggest `exiftool -all=`. ExifTool is an excellent metadata editor, and with extra
options it can keep color data, but the one-liner and MagicDispel give very different results.
Tested on real photos with ExifTool 13.55 on macOS:

| Photo | `exiftool -all=` | MagicDispel |
| --- | --- | --- |
| iPhone portrait-mode HEIC | Removes the capture tags but keeps all 10 hidden images of the tested photo: depth map, portrait matte, skin, hair, teeth, glasses and sky mattes, style map and thumbnails, plus a 58 KB Apple property list | Removes every hidden image with its bytes and keeps only the HDR gain map. Looks identical |
| iPhone HDR JPEG | Also removes the HDR gain map, the HDR headroom and the color profile: the photo loses its HDR and its colors shift | Removes the private data; keeps the gain map, headroom and colors. Looks identical, in SDR and HDR |
| Mac screenshot | Also removes the color profile and the DPI: colors shift, and the image opens at twice its size | Removes the display's name, model and serial number from the profile; keeps colors and DPI. Looks identical |

"Looks identical" means identical renders in macOS ImageIO and ColorSync: pixels, sRGB,
Display P3, HDR, gain maps, orientation and DPI.

## How it works

1. **Identify** the format from the file's contents, not its name.
2. **Rebuild** a new file from an allowlist: the image data and the few fields needed to show
   it, such as color, orientation and HDR, some of them written afresh. Everything else is
   left behind.
3. **Verify** the result. It must parse on its own, decode to identical frames (for HEIC,
   every image item is compared byte for byte), and satisfy ExifTool when it is installed. The
   original must not have changed in the meantime.
4. **Save** the copy next to the original under a new name, without ever overwriting a file.

Files that cannot be rebuilt safely, such as damaged files, RAW photos or unknown structures,
are refused with an explanation instead of being passed through.
[Privacy and format details](https://github.com/v1nc3nt-continualab/magicdispel/blob/main/docs/PRIVACY.md)
describe every check.

## What is kept and removed

| Format | Kept | Removed |
| --- | --- | --- |
| JPEG | image data, JFIF density, orientation, DPI, color space, ICC profile; HDR gain-map images (MPF), Apple HDR headroom and gain-map XMP | other EXIF and XMP, IPTC/Photoshop, comments, C2PA, thumbnails, maker notes, trailing data |
| PNG, APNG | image data, palette, transparency, color chunks (sRGB, gAMA, cHRM, cICP, HDR), DPI, animation, ICC profile, orientation | text, time stamps, C2PA, private chunks, anything after the end |
| HEIC, HEIF | image items and tiles, HDR gain maps (Apple and ISO), alpha, orientation, ICC profile, HDR XMP fields | EXIF, other XMP, Apple property lists, depth and calibration, portrait and semantic mattes, style maps, thumbnails, item descriptions and times, unused data |
| AVIF | as HEIF, including animations; sequence times, names and user data are cleared | as HEIF |
| WebP | image data, alpha, animation, ICC profile, orientation | other EXIF, XMP, unknown chunks |
| GIF | images, palettes, frame timing, transparency, loop count, ICC profile | comments, text overlays, XMP, other extensions |
| TIFF | image data, decoding tags, DPI, orientation, page numbers, ICC profile | EXIF and GPS directories, XMP, IPTC, Photoshop, descriptions, private tags, sub-images |
| BMP | converted to lossless PNG with the same pixels, DPI and profile | everything else |
| RAW, video, PDF | not supported; RAW files built on TIFF (DNG, CR2, NEF...) are recognized and refused | |

ICC profiles keep their color data. Their date becomes a fixed placeholder (`2000-01-01`),
their description `Clean`, and device and creator fields are cleared. Images up to 268
megapixels are checked, enough for 200-megapixel phone photos; larger ones are refused.

## File names

A cleaned copy is named after its original with `_clean` added, but without the dates, times
and timestamps that screenshots, phone cameras and chat apps put in names:

| Original | Cleaned copy |
| --- | --- |
| `Screenshot 2026-09-23 at 15.14.15.png` | `Screenshot_clean.png` |
| `IMG_20240501_123456.jpg` | `IMG_clean.jpg` |
| `mmexport1714567890123.jpg` | `mmexport_clean.jpg` |
| `IMG_1234.HEIC` | `IMG_1234_clean.HEIC` |

Further copies get `_clean_1`, `_clean_2`... `--keep-name` keeps the original name as it is,
and `--anonymous` uses a random name such as `photo_3f9c...e1.jpg` instead.

## Usage

```sh
magicdispel photo.jpg "Screenshot 2026-09-23 at 15.14.15.png" portrait.heic
magicdispel --anonymous photo.jpg
magicdispel --check
```

| Option | |
| --- | --- |
| `--anonymous` | name copies `photo_<random>.jpg` instead of after the original |
| `--keep-name` | keep the original's name, dates and times included |
| `--check` | check that everything needed is installed, and whether ExifTool's second check is on |
| `--version` | show the version |
| `-h`, `--help` | show all options |

Quote paths containing spaces, or drag the files into the terminal. Folders are not
processed recursively. A file whose name starts with `-` goes after `--`:
`magicdispel -- -photo.jpg`. Exit codes: `0` success or help, `1` one or more files not
cleaned, `2` invalid arguments, `130` interrupted. A failed file never stops the rest of a
batch.

## More ways to install

With [uv](https://docs.astral.sh/uv/) or [pipx](https://pipx.pypa.io/) and Python 3.10 or
newer:

```sh
uv tool install magicdispel
pipx install magicdispel
```

The one-line installers are short scripts you can read first:
[install.sh](https://github.com/v1nc3nt-continualab/magicdispel/blob/main/install.sh) and
[install.ps1](https://github.com/v1nc3nt-continualab/magicdispel/blob/main/install.ps1). They
install uv if needed, which brings its own Python when the system has none that fits. If
`magicdispel` is not found afterwards, open a new terminal window.

To update, run the installer again or `uv tool upgrade magicdispel`. To remove MagicDispel, run
`uv tool uninstall magicdispel`.

[ExifTool](https://exiftool.org/) is optional. When version 12.73 or newer is installed,
MagicDispel uses it to double-check every result: `brew install exiftool`,
`sudo apt install libimage-exiftool-perl` or `winget install --exact --id OliverBetz.ExifTool`.
If it is installed somewhere unusual, set `MAGICDISPEL_EXIFTOOL` to its full path.

## Questions

**Does it change my original photo?**
No. MagicDispel only reads the original. The clean copy is a new file next to it, and no
existing file is ever overwritten.

**Does anything leave my computer?**
No. MagicDispel never uses the network. Only the installer downloads uv and MagicDispel
itself.

**Why is a cleaned HEIC about as large as the original?**
HEIC files are cleaned in place, so every offset inside them stays valid: removed data is
overwritten with zeros rather than cut out. It is gone, but its space remains.

**Why was my file refused?**
When a file cannot be rebuilt safely, MagicDispel says why and saves nothing rather than
guess. The file may be damaged, use a structure MagicDispel does not know, or be a RAW photo:
export RAW photos as JPEG or HEIC first.

**Can I still edit portrait effects afterwards?**
No. Depth maps and portrait mattes are removed, so Photos can no longer change the portrait
blur or lighting of the copy. Keep the original if you may want to edit it later.

**Does it make me anonymous?**
No. What the picture shows can still identify people and places: faces, signs, reflections,
the view from a window. So can a match with a copy published before, or the account it is
shared from. MagicDispel removes metadata; it does not change the picture.

## Development

```sh
python -m venv .venv
# Activate the environment for your shell.
python -m pip install -e ".[test]" build
python -m unittest discover -s tests -v
python -m build
```

The tests need only Pillow. When ExifTool is installed, they also add metadata the way other
programs write it and let ExifTool double-check each result. No personal photos are included.

Before and after any change to the cleaning code, run the regression harness over a folder of
real sample photos kept outside the repository:

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
3.13, with and without ExifTool, and the install scripts on all three. `release.yml` publishes
a tagged version to PyPI once those pass. `scripts/screenshot.py` renders the images in this
README.

## License and attribution

MagicDispel is designed by VincentC and MIT licensed. [ExifTool](https://exiftool.org/), an
optional companion, is developed by Phil Harvey and distributed separately under its own
license. MagicDispel is not affiliated with ExifTool.
