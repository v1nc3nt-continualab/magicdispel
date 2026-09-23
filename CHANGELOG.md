# Changelog

## 0.1.0 (unreleased)

- Draft command-line package targeting macOS, Windows and Linux.
- Local metadata cleaning for JPEG, PNG/APNG, HEIC/HEIF, AVIF, WebP, GIF and TIFF.
- Lossless BMP-to-PNG conversion and frame/page verification for GIF/APNG/TIFF.
- Correct handling of lossless and extended WebP format names.
- MPF/HDR JPEG support with per-image metadata cleaning, HDR preservation and rebuilt indexes.
- Recognize HEIF and AVIF sequence file types; verify animated AVIF frames and clear container dates.
- Handle JPEG-compressed TIFF strip aliases and verify all encoded TIFF strips/tiles directly.
- Image-data hash checks, original preservation and collision-safe output names.
- Preserve necessary HDR fields while removing recognized HEIF depth/calibration,
  portrait/semantic masks, thumbnails and editing-only style maps with their actual bytes.
- Protect shared image dependencies and reject unsupported auxiliary layouts.
- Remove HEIF XMP toolkit strings and unused item properties.
- Add `--anonymous` random output filenames with collision protection.
- Add synthetic auxiliary-graph and filename tests; prepare, but do not yet execute,
  the Windows/Linux/macOS CI matrix.
- Explicit dependency checks and English/Chinese installation documentation.
