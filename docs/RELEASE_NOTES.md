Unpublished draft: MagicDispel 0.1.0 cleans common photo metadata on your computer.

Supported formats: JPEG, PNG/APNG, HEIC/HEIF, AVIF, WebP, GIF, TIFF and BMP.
BMP becomes lossless PNG; other formats retain their image encoding. Originals are preserved,
output names never overwrite existing files, and image data is verified before saving results.

Requires Python 3.10+ and ExifTool 12.73+; ExifTool 13.55+ is recommended for recent iPhone HEIC.
After installing those dependencies, install with pipx:

```sh
pipx install "https://github.com/v1nc3nt-continualab/magicdispel/archive/refs/tags/v0.1.0.zip"
magicdispel --check
magicdispel photo.jpg
```

English and Chinese setup instructions for macOS, Windows and Linux are in the repository.

This draft preserves color, orientation, HDR and transparency. It removes recognized
HEIF editing-only auxiliary images, depth/calibration, masks and previews with their
payloads. Later portrait/depth/style adjustments may be reduced. `--anonymous` uses
a random output filename without carrying over the original name.
It is not a zero-metadata/anonymity tool; read the privacy limitations before sharing sensitive photos.

Release remains paused. Windows and Linux have not been tested on those systems here;
the prepared CI matrix must run before publication. No release tag or download exists yet.
