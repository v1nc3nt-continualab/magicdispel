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
- Clean macOS screenshots, whose display profiles carry Apple parametric curves
  (`aarg`/`aagg`/`aabg`, kept) and display identity/setup tags (`dscm`, `mmod`,
  `ndin`, `vcgt`, `vcgp`, removed). Previously these files were refused.
- Command line in English and Simplified Chinese, following the system language
  or `MAGICDISPEL_LANG`; rewritten help screen.
- Regression harness (`scripts/regression.py`) for local sample corpora, with macOS
  ImageIO/ColorSync render checks and synthetic leak probes (`scripts/make_probes.py`).
- PNG, APNG and BMP are rebuilt by MagicDispel itself from an allowlist of the chunks
  needed for display, instead of asking ExifTool to delete known metadata. Private and
  unknown chunks, C2PA manifests, text, time stamps and data after the image end are
  now removed; DPI (`pHYs`) and color chunks (`sRGB`, `gAMA`, `cHRM`, `cICP`) are kept,
  so Retina screenshots keep their size. Compressed image data must hold exactly the
  image, with no extra bytes. BMP-to-PNG conversion keeps the DPI. These formats no
  longer need ExifTool, which still double-checks results when installed.
- JPEG is rebuilt the same way. Decoding segments and HDR data (ISO 21496-1 gain-map
  metadata, Apple gain curves) are copied unchanged; JFIF, EXIF, XMP, the ICC profile
  and the MPF index are written afresh with only display fields: orientation, DPI,
  color space, Apple HDR headroom/gain and recognized gain-map XMP. Comments,
  IPTC/Photoshop, C2PA, private segments, JFIF and EXIF thumbnails, MPF image IDs and
  trailing data are removed. JPEG DPI is now kept. iPhone HDR JPEGs render identically
  in macOS (SDR, HDR and gain maps).
- WebP and GIF are rebuilt the same way. WebP keeps its image, alpha and animation
  chunks, a sanitized ICC profile and the orientation; XMP, unknown chunks (including
  inside animation frames) and trailing data are removed, and files that no longer
  need the extended header are written in the simple format. GIF keeps images, color
  tables, frame timing, transparency, the loop count and a sanitized ICC profile;
  comments, plain-text overlays, XMP and other application extensions are removed.
  Reserved bits in WebP and GIF headers are cleared.
- HEIC/HEIF and AVIF are cleaned without ExifTool. EXIF, URI, JUMBF and non-XMP MIME
  items are removed with their bytes (orientation lives in HEIF's irot/imir); XMP items
  keep only recognized HDR fields; unknown item types are refused. Top-level boxes
  other than ftyp, meta, moov and mdat (such as uuid XMP) are emptied in place or
  dropped from the end; bytes in mdat and idat that no item or sample uses are zeroed.
  In image sequences, creation/modification times, handler and compressor names, user
  data and metadata boxes are cleared, and external media references are refused.
- TIFF is written afresh: each page keeps its image data and the tags needed to decode
  and show it, with a sanitized ICC profile; EXIF and GPS directories, XMP, IPTC,
  Photoshop blocks, descriptions, private tags, sub-images and free space are removed.
  Every byte of the result is accounted for. BigTIFF and old-style JPEG are refused.
- **ExifTool is no longer required.** Every format is rebuilt by MagicDispel itself;
  when ExifTool 12.73+ is installed it double-checks each result, and `--check` reports
  whether that second check is on. The ExifTool-based cleaning pipeline is removed.
