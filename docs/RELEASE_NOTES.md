Unpublished draft: MagicDispel 0.1.0 removes private metadata from photos on your computer,
without touching image quality.

Install it with one command. On macOS and Linux:

```sh
curl -LsSf https://raw.githubusercontent.com/v1nc3nt-continualab/magicdispel/main/install.sh | sh
```

On Windows, in PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/v1nc3nt-continualab/magicdispel/main/install.ps1 | iex"
```

Or, with uv or pipx: `uv tool install magicdispel` or `pipx install magicdispel`.

Then type `magicdispel` and a space, drag photos into the terminal, and press Enter. A cleaned
copy appears next to each original; originals are never modified and nothing is overwritten.

Supported formats: JPEG, PNG/APNG, HEIC/HEIF, AVIF, WebP, GIF, TIFF and BMP. Each file is
rebuilt from only what is needed to show it, so location, capture time, camera details,
author, comments, thumbnails, depth maps and portrait mattes, C2PA manifests and unknown data
blocks are left behind. Image data is copied unchanged (BMP becomes lossless PNG), and color,
orientation, DPI, transparency, animation and HDR gain maps are kept. The dates and times that
screenshots and cameras put in file names are left out of the new name. Every result is
checked before it is saved. ExifTool is optional; when installed, it double-checks each result.

This is not an anonymity tool: what a picture shows can still identify people and places. Read
the privacy details before sharing sensitive photos.

Release remains paused until Windows and Linux have been tested on those systems. No release
tag or download exists yet.
