Unpublished draft: MagicDispel 0.1.0 removes private metadata from photos on your computer,
without touching image quality.

Type `magicdispel` and a space, drag photos into the terminal, and press Enter. A cleaned copy
appears next to each original; originals are never modified and nothing is overwritten.

Supported formats: JPEG, PNG/APNG, HEIC/HEIF, AVIF, WebP, GIF, TIFF and BMP. Each file is
rebuilt from only what is needed to show it, so location, capture time, camera details,
author, comments, thumbnails, depth maps and portrait mattes, C2PA manifests and unknown data
blocks are left behind. Image data is copied unchanged (BMP becomes lossless PNG), and color,
orientation, DPI, transparency, animation and HDR gain maps are kept. Every result is checked
before it is saved.

Requires Python 3.10+. ExifTool is optional; when installed, it double-checks each result.

```sh
pipx install "https://github.com/v1nc3nt-continualab/magicdispel/archive/refs/tags/v0.1.0.zip"
magicdispel --check
magicdispel photo.jpg
```

Instructions for macOS, Windows and Linux are in the repository. This is
not an anonymity tool: what a picture shows can still identify people and places. Read the
privacy details before sharing sensitive photos.

Release remains paused until Windows and Linux have been tested on those systems. No release
tag or download exists yet.
