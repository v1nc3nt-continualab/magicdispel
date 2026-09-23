# MagicDispel

Clean common photo metadata locally while preserving image quality.

**Unpublished draft:** release and installation instructions below are pending validation and publication.

[中文说明](README.zh-CN.md)

```console
magicdispel photo.jpg
magicdispel "photo one.heic" "photo two.png"
magicdispel --anonymous photo.jpg
```

Type `magicdispel`, add a space, drag one or more photos into your terminal,
then press Enter. A new `photo_clean.jpg` appears next to the original.
Existing files are never overwritten: subsequent copies use `_clean_1`, `_clean_2`, etc.
Use `--anonymous` to generate `photo_<random>.jpg` without retaining the input filename.
This changes the output name only; it does not anonymize visible image content.

## What it does

- Removes common GPS, capture-time, device, author, comment, EXIF, IPTC and XMP metadata.
- Keeps the original image encoding except BMP, which becomes lossless PNG.
- Verifies image-data hashes and checks decoded frames for GIF/APNG/TIFF and BMP conversion.
- Preserves orientation, ICC color conversion data, HDR gain maps and transparency.
- Rebuilds ICC identity fields and removes HEIF URI metadata items, including Apple style property lists.
- Removes recognized HEIF thumbnails, depth/calibration data, semantic/portrait masks and editing-only style maps.
- Erases removed auxiliary payload bytes and unused properties, while protecting shared image tiles.
- Checks for unexpected remaining metadata and refuses outputs that fail verification.
- Processes files on your computer. No upload service, telemetry or network requests in the tool.

**This is not a zero-metadata or anonymity tool.** Sanitized ICC profiles, minimal EXIF display tags,
HDR/alpha auxiliary images and recognized HDR XMP remain. ICC dates use the fixed privacy
placeholder `2000-01-01 00:00:00`, descriptions become `Clean`, and original creator/device/profile
identifiers are cleared. Recognized Apple adaptive-curve image identifiers are also cleared.
Removing auxiliary data reduces later portrait/depth/style editing capabilities;
tested SDR/HDR rendering was unchanged. The image itself, its default filename, or a match with a previously
published image may still identify a person or place.
See [privacy and format limits](docs/PRIVACY.md).

## Install

Python **3.10+** and [ExifTool **12.73+**](https://exiftool.org/install.html) are required.
ExifTool **13.55+** is recommended for recent iPhone HEIC files. ExifTool is a separate
system dependency; installing the Python package alone does not install it.

The first release can be installed directly from GitHub. A PyPI listing is not required.

### macOS

With [Homebrew](https://brew.sh/) installed:

```sh
brew install exiftool pipx
pipx ensurepath
pipx install "https://github.com/v1nc3nt-continualab/magicdispel/archive/refs/tags/v0.1.0.zip"
```

Open a new terminal, then run `magicdispel --check`.

### Windows (PowerShell)

Install Python and ExifTool using Windows Package Manager, or use their official installers:

```powershell
winget install --exact --id Python.Python.3.12
winget install --exact --id OliverBetz.ExifTool
```

Open a new PowerShell window, then:

```powershell
py -3.12 -m pip install --user pipx
py -3.12 -m pipx ensurepath
py -3.12 -m pipx install "https://github.com/v1nc3nt-continualab/magicdispel/archive/refs/tags/v0.1.0.zip"
```

Open another terminal and run `magicdispel --check`.
If you use ExifTool's ZIP distribution, keep `exiftool_files` beside the executable
and rename `exiftool(-k).exe` to `exiftool.exe` as its installation instructions describe.

### Linux

On Ubuntu 24.04+ / a recent Debian release:

```sh
sudo apt update
sudo apt install pipx libimage-exiftool-perl
pipx ensurepath
pipx install "https://github.com/v1nc3nt-continualab/magicdispel/archive/refs/tags/v0.1.0.zip"
```

Open a new terminal and run `magicdispel --check`. If your distribution provides an older
ExifTool, upgrade it using the [official installation instructions](https://exiftool.org/install.html).

### Already using uv?

After installing ExifTool:

```sh
uv tool install --python 3.12 "https://github.com/v1nc3nt-continualab/magicdispel/archive/refs/tags/v0.1.0.zip"
```

## Usage

```sh
magicdispel --check
magicdispel --version
magicdispel photo.jpg screenshot.png portrait.heic
magicdispel --anonymous photo.jpg
magicdispel -- "-filename-starts-with-a-dash.jpg"
```

Use a space after the command. Quote paths containing spaces, or drag the files into
the terminal. On Windows, drag-and-drop support depends on your terminal; a quoted path
always works. Directories and recursive processing are not supported.

Outputs stay in the input folder and keep the same image format, except BMP becomes PNG. Exit codes:
`0` success/help, `1` one or more processing failures, `2` invalid command-line arguments,
`130` interrupted. A failed item does not prevent the remaining items in a batch from running.

If ExifTool is not on PATH, set `MAGICDISPEL_EXIFTOOL` to the full executable path.
`--check` verifies that it can run and meets the minimum version.

## Formats

| Format | Behavior |
| --- | --- |
| JPEG/JPG | Supported, including MPF/HDR auxiliary images; every image is cleaned and the MP index is rebuilt |
| PNG/APNG | Supported; animation is preserved |
| HEIC/HEIF | Keeps HDR/alpha; removes recognized editing-only auxiliaries and thumbnails, without recompressing retained images |
| AVIF | Supported, including animated AVIF; frames and timing are verified |
| WebP | Supported, including lossless and animated WebP |
| GIF | Supported; transparency and animation are preserved |
| TIFF/TIF | Supported, including JPEG compression; encoded strips/tiles, pages and bit depth are verified |
| BMP | Converted to PNG with decoded pixels verified unchanged |
| RAW, video, PDF | Not supported |

Not every file variant will pass verification. Warnings, unexpected metadata or altered
image hashes cause the tool to stop that file without publishing a result.

## Development

```sh
python -m venv .venv
# Activate the environment for your shell.
python -m pip install -e ".[test]" build
python -m unittest discover -s tests -v
python -m build
```

Integration tests generate synthetic images and require ExifTool. No personal photos
are included. The local workflow `.github/workflows/test.yml` is prepared for macOS,
Windows and Linux with Python 3.10/3.13. It has not been dispatched: Windows/Linux
execution remains a release prerequisite. Local macOS results do not establish support
for those systems. Synthetic HEIF graph tests do not replace actual HEIC decoder tests.

## License and attribution

MagicDispel is MIT licensed. [ExifTool](https://exiftool.org/) is developed by Phil Harvey
and distributed separately under its own license. MagicDispel is not affiliated with ExifTool.
