# Changelog

## Unreleased

Videos: MP4 and QuickTime (MOV) files are cleaned too, without changing a single frame. Tested
on 64 public sample videos, GoPro, iPhone (Dolby Vision, spatial video), Pixel and Samsung ones
among them: 48 clean, FFmpeg decodes identical frames from each, and macOS plays each the same;
the other 16 are refused as designed.

- Kept: video and sound tracks with every sample, decoder configurations, rotation, edit lists,
  color, HDR, Dolby Vision and Apple's spatial video information.
- Removed: location, device, software and dates in user data and metadata boxes; timed
  metadata tracks (GPS and motion, face detection, Live Photo and motion photo data), timecode
  and chapter tracks, with their samples; maker data such as GoPro's serial numbers and
  Samsung's SEF data; creation times, handler, vendor and compressor names; unused media data
  and anything after the movie.
- Refused: fragmented, encrypted and audio-only files, subtitle tracks, codecs and sample
  entry boxes not on the list, and a removed track that another needs to be shown.
- A video is cleaned in a copy next to the original, never read into memory: a 4.7 GB video
  takes under five seconds. ExifTool's second check reads that copy from its path.
- A video keeps its extension, .mp4 or .mov, whatever its content: players may read one file
  differently by its extension. `--anonymous` names videos `video_<random>.mov`.
- Image sequences (HEIF, AVIF) and videos share one cleaner, which also checks that headers and
  sample tables have exactly their size, and that nothing in the movie box changed but the
  cleared fields. The 95 test photos clean exactly as before.
- The regression harness checks videos with FFmpeg (every decoded frame and stream) and macOS
  AVFoundation (tracks, rotation, HDR, frames).

## 0.1.4 (2026-09-24)

Tested on 27 photos from 21 current phones and cameras (Samsung, Google Pixel, Xiaomi, Huawei,
Honor, vivo, OnePlus, OPPO, Canon, Nikon, Sony, Fujifilm, Panasonic, OM System, Leica): all of
them clean, render identically in macOS (SDR, HDR, gain maps, DPI), and keep none of the 5 to
90 private fields each one carried.

- Photos from some Pixel phones, such as the Pixel 9 Pro, were refused ("would not look
  identical"). Their XMP declares the gain map as an element, and Pillow decides whether a
  JPEG is an Ultra HDR photo, and so how many images it shows, from how that XMP is written.
  Multi-picture JPEGs are now compared picture by picture, each as a JPEG of its own, so how
  a decoder shows the whole no longer matters; this also decodes and compares every gain map.
- The regression harness no longer flags JPEG photos whose preview was dropped, such as those
  from Canon, Panasonic and Sony cameras; other formats must still keep every frame.

## 0.1.3 (2026-09-24)

Android's Ultra HDR photos clean again: 0.1.2 refused them. Tested with Google's own Ultra
HDR samples (libultrahdr and Skia): every one now cleans, and renders identically in macOS,
in SDR and HDR.

- 0.1.2 refused Ultra HDR photos as "cannot be decoded to check the result". Pillow shows such
  a photo as its primary image alone, without the gain map, and the pixel check asked it for
  the gain map. The check now compares the frames Pillow shows; the gain map's own data is
  still checked byte for byte.
- ISO 21496-1 gain-map metadata was refused as "unknown flags" when a phone set its reserved
  flag bits. Reserved bits are now ignored, as the reference decoders (libavif, libultrahdr)
  ignore them. In JPEG, anything after the fields the standard defines is dropped instead of
  refusing the photo. Metadata with a zero denominator, which decoders reject, is still refused.
- HDR fields that a JPEG keeps in extended XMP (the continuation of a large packet, which the
  main packet names) are kept, in the one fresh packet.

## 0.1.2 (2026-09-24)

Fixes from an independent review, which built files that hid data where 0.1.1 did not look.
Photos as cameras, phones and editors write them were not affected: the 60 test photos
clean exactly as before.

- JPEG multi-picture files keep only the photo and its HDR gain maps. Previews, which may show
  more than the cropped photo, are removed; other extra images, such as stereo pairs, are
  refused. A gain map must have the photo's proportions.
- ICC color tags must match their type's layout exactly: anything after a curve, in reserved
  fields or between the parts of a lookup table is refused, as are floating-point transforms
  (D2Bx/B2Dx), which are not checked byte for byte. Apple's legacy HDR curve is checked the same way.
- ISO 21496-1 gain-map metadata, in JPEG and in HEIF/AVIF `tmap` items, must have exactly the
  standard's layout, and version 0.
- Image sequences (animated AVIF and HEIF) keep only the boxes that play them, from a fixed list.
  Before, only known metadata boxes were emptied, so an unknown private box stayed unless
  ExifTool was installed to catch it. Tracks other than pictures and their alpha are refused.
- The regression harness counts a missing macOS render as a failure, not as a match, and does
  not reuse incomplete results.
- The package description on PyPI matches the README's summary: "Remove private metadata
  from photos on your own computer, without changing a single pixel".

## 0.1.1 (2026-09-24)

- The PyPI page shows the new README: the logo, a demo session, a comparison with
  `exiftool -all=` measured on real photos, how cleaning works, and common questions.
- Python version classifiers for 3.10 to 3.13. The cleaning itself is unchanged.

## 0.1.0 (2026-09-24)

- Command-line package for macOS, Windows and Linux.
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
- Synthetic auxiliary-graph and filename tests, run by CI on macOS, Windows and Linux.
  The tests need only Pillow; with ExifTool installed they also check each result with it,
  and CI runs them both ways.
- Explicit dependency checks and installation documentation.
- Clean macOS screenshots, whose display profiles carry Apple parametric curves
  (`aarg`/`aagg`/`aabg`, kept) and display identity/setup tags (`dscm`, `mmod`,
  `ndin`, `vcgt`, `vcgp`, removed). Previously these files were refused.
- One-command installers for macOS/Linux (`install.sh`) and Windows (`install.ps1`). They
  install uv when needed, then MagicDispel, and end by showing the logo. CI runs them on
  all three systems and cleans a screenshot with the installed command; tagging a version
  publishes to PyPI through trusted publishing.
- Rewritten help screen, in English. Running `magicdispel` on its own shows the
  MagicDispel logo in color with the credits, the tagline and how to use it; outside a
  terminal, or with `NO_COLOR` set, it is plain text.
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
- HEIF items are read by one parser and removed in one pass. Damaged HEIF files are now
  reported as damaged; boxes in the item container other than the item tables (such as
  XML boxes) are emptied; image sequences without an item container are accepted; and
  the check before saving also confirms that no editing image, thumbnail or item name
  remains.
- Photos up to 268 megapixels are checked, including 200-megapixel phone photos; larger
  ones are refused with a message. Previously anything over 179 megapixels stopped the
  whole batch with a Python error. The pixel comparison now uses the decoded samples at
  full precision (16-bit included) and hashes them in strips, never copying a whole frame:
  a 200-megapixel photo is checked in under a second with about 0.9 GB of memory.
- Multi-picture JPEGs (such as iPhone HDR photos) that ExifTool has added metadata to are
  accepted. ExifTool leaves the first image's size in the index as it was; that size is no
  longer relied on, while MagicDispel's own index is still checked exactly.
- HEIC files that ExifTool's `-all=` has already processed are accepted. ExifTool leaves
  their EXIF and XMP items in place with no data; such empty metadata items are removed
  like any other. Previously these files were refused as damaged.
- Output names leave out the dates, times and timestamps that screenshots, phone cameras
  and chat apps put in file names, which told when a picture was taken: `Screenshot
  2026-09-23 at 15.14.15.png` becomes `Screenshot_clean.png`, `IMG_20240501_123456.jpg`
  becomes `IMG_clean.jpg`, `mmexport1714567890123.jpg` becomes `mmexport_clean.jpg`. The
  rest of the name stays. `--keep-name` keeps the name as it is.
- Hardening after an audit with crafted files and 4,000 mutated samples:
  - HEIF item properties are now allowlisted. Only those needed to decode and show an image
    stay, and fixed-size ones must have exactly their size. Descriptions (`udes`), creation
    and modification times (`crtt`, `mdft`), camera parameters and unknown properties were
    kept before and are now removed. An unknown property marked essential is refused.
  - RAW photos built on TIFF (DNG, CR2, NEF and others) are refused with a clear message.
    Before, a DNG was "cleaned" into a copy of its small preview.
  - TIFF pages must hold exactly the strips or tiles their image needs, uncompressed ones at
    exactly their size, and kept tags exactly the number of values the specification gives
    them. Extra strips and values could carry hidden bytes.
  - PNG palettes and transparency chunks must hold exactly what the color type allows
    (an oversized tRNS chunk could carry hidden bytes). Images over the pixel limit are
    refused before any data is inflated, and APNG frames must lie within the image.
  - Any failure of Pillow's decoders now reads "cannot be decoded to check the result"
    instead of an unexpected error (damaged AVIF files raised RuntimeError), and Pillow's
    warnings about damaged files no longer appear in the terminal.
- ExifTool's second check reads each result from a pipe instead of a temporary file next to
  the photo. On Windows, a photo in a folder with Chinese or other non-ASCII characters
  failed the check, because Windows passes paths to ExifTool in its legacy code page.
- A photo that fails unexpectedly is reported as an unexpected error and no longer stops
  the rest of a batch. A result its own format cannot read back counts as failing
  verification.
