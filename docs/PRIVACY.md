# Privacy and format details

MagicDispel rebuilds each file from an allowlist: it copies only the parts a viewer needs to
show the image and leaves everything else behind. It does not search for known metadata to
delete, so metadata it has never heard of is removed too, and the parts it keeps must match
their exact layout, so they cannot carry anything else along. It does not promise forensic
anonymization.

## What is kept, and why

- **Image data.** Compressed pixels are copied byte for byte. BMP is the only format that is
  re-encoded, losslessly, as PNG.
- **Color.** ICC profiles keep their color conversion data: matrices, curves, lookup tables,
  Apple's parametric curves in screenshot profiles, and HDR adaptive curves (with their
  image-specific identifier cleared). Every profile is rebuilt with the fixed date
  `2000-01-01 00:00:00` and description `Clean`; creator, maker, model, CMM, platform and profile
  ID are cleared, and display calibration data is removed. Color declarations of the formats
  themselves are kept: PNG sRGB, gAMA, cHRM, cICP and HDR chunks, HEIF color boxes.
- **Display fields.** Orientation, DPI, color space and the DCF interoperability index, written
  into a fresh EXIF block holding nothing else. For iPhone HDR photos, Apple's HDR headroom and
  gain, in a fresh maker note holding nothing else.
- **HDR.** Gain-map images (JPEG multi-picture files, HEIF auxiliary images and ISO `tmap`
  items), ISO 21496-1 gain-map metadata, Apple gain curves, and recognized numeric gain-map XMP
  fields. Of the images in a JPEG multi-picture file, only the photo and gain maps with its
  proportions are kept.
- **Structure.** Transparency, animation frames, timing and loop count, TIFF pages and page
  numbers, and the format's own headers.
- **The file name**, with `_clean` added and without the dates, times and timestamps that
  screenshots, cameras and chat apps put in names: `Screenshot 2026-09-23 at 15.14.15.png`
  becomes `Screenshot_clean.png`, `IMG_20240501_123456.jpg` becomes `IMG_clean.jpg`.
  `--keep-name` keeps the name as it is; `--anonymous` replaces it with a random 128-bit token
  that contains no name, time, MAC address or user ID.

## What is removed

Everything not listed above, including: EXIF capture time, camera, lens, serial numbers,
location and maker notes; XMP (except gain-map fields); IPTC and Photoshop blocks; comments and
text chunks; C2PA manifests; embedded thumbnails and previews, including the preview images
of JPEG multi-picture files, which may show an uncropped original; HEIF depth maps, lens
calibration, portrait and semantic mattes, style maps, Apple property lists, item names, and
item properties such as descriptions, creation times and camera parameters; JPEG MPF image
IDs; in image sequences, every box not needed to play them, and creation times, handler and
encoder names; TIFF EXIF and GPS directories, descriptions, private tags and sub-images; and
unknown or private data blocks and data after the end of an image.

Removing auxiliary HEIF images limits later portrait, depth-of-field and photographic-style
edits. Tested HEIC and HDR JPEG files render identically on macOS in SDR and HDR.

## How nothing slips through

- **Rebuild, not delete.** Each format has its own rebuilder (`src/magicdispel/formats/`). Parts
  with a fixed size must have exactly that size, so they cannot carry extra bytes. PNG image data
  must inflate to exactly the scanlines the header describes, with nothing after the compressed
  stream, and its palette and transparency hold exactly what the color type allows. A TIFF page
  holds exactly the strips or tiles its image needs, uncompressed ones at exactly their size, and
  every kept tag has exactly the number of values the specification gives it. HEIF item
  properties are kept only if they say how to decode and show an image. JPEG multi-picture
  indexes are written fresh. Every kept ICC color tag and Apple HDR curve must match its type's
  layout: a byte outside it that is not zero, after a curve, in a reserved field or between
  the parts of a lookup table, gets the file refused. ISO 21496-1 gain-map metadata keeps only
  the fields its standard defines, which are all a decoder reads: in JPEG anything after them
  is dropped, and in HEIF, where an item cannot be shortened in place, it gets the file refused.
- **Image sequences.** Animated AVIF and HEIF files keep only the boxes on a fixed list:
  headers, tracks, edits and sample tables, and in each sample entry its decoder
  configuration and color and display properties. Readers skip boxes they do not know, so
  any other box, however it is named, is emptied rather than kept.
- **HEIF in place.** HEIF files are cleaned without moving any image data, so every offset
  stays valid: the item tables are rewritten in the space they had, removed items and boxes are
  zero-filled, bytes that no remaining item or sample uses are zeroed, and boxes at the end of
  the file are dropped. This is why HEIC files do not shrink much.
- **Fail closed.** Anything that cannot be handled safely is refused, not passed through: an
  unknown critical PNG chunk, an unknown HEIF item type or auxiliary image, a HEIF property of
  unknown meaning that readers may not ignore, an image that depends on a removed layer, image
  groups other than alternatives (such as the stereo pairs of spatial photos), JPEG
  multi-picture images other than previews and gain maps (such as stereo pairs), sequence
  tracks other than pictures and their alpha, fragmented image sequences, media stored outside
  the file, BigTIFF, old-style JPEG in TIFF, metadata inside JPEG-compressed TIFF strips,
  unrecognized ICC tags and floating-point ICC transforms, gain-map metadata of an unknown
  version. RAW photos built on TIFF (DNG, CR2, NEF and others) are refused too: their TIFF
  pages hold only a preview.
- **Checked before saving.** The rebuilder parses its own result independently and compares it
  with what the original should yield: for HEIF, for instance, that every retained image item is
  byte-identical, XMP holds only gain-map fields, no editing image, thumbnail or item name
  remains, profiles are sanitized, and unused bytes are zero; for TIFF, that no byte of the
  file is unaccounted for. Pillow must then decode identical pixels, frames, timing and
  transparency (all formats but HEIC; for JPEG, of the images kept). If ExifTool 12.73+ is
  installed, it reads the result as a second opinion, and any warning, private field or data it
  cannot identify stops the save. The original's hash is compared before and after, so a file
  changed by another program during cleaning is not published.

## Out of scope

Data hidden inside the compressed image data itself, for example in JPEG scans, VP8 or HEVC
frames, GIF LZW data or unused palette entries, is copied along with the image. Detecting such
steganography is beyond this tool.

## File-system information

Outputs are new files containing only the verified bytes. Extended attributes, macOS resource
forks and Windows alternate data streams of the original are not copied; macOS output
attributes are cleared, as are Linux `user.` attributes where supported. Normal permissions and
creation/modification times of the new file are set by the system.

## What this cannot prevent

- Location or identity inferred from what the picture shows: faces, signs, documents,
  scenery, reflections.
- Matching the image with a previously published or known original.
- Identification through the account or service used to share the result.
- Information a sharing application adds afterwards.
- Deliberately hidden information, steganography or sensor fingerprinting.

When anonymity matters, share a separate copy and look at what it shows.

## Validation status

On macOS, the unit tests and a local corpus of 60 real and synthetic samples pass: every output
renders identically in macOS ImageIO/ColorSync (pixels, sRGB and Display P3 renders, SDR, HDR,
gain maps, orientation and DPI), and 11 synthetic leak probes come out clean. Files built by an
independent review to hide data where 0.1.1 did not look (a preview in a multi-picture JPEG,
bytes after an ICC curve, in ISO gain-map metadata and in an image-sequence box) are cleaned or
refused since 0.1.2, and 634 ICC profiles from macOS and the corpus sanitize exactly as
before. On macOS,
Windows and Linux, CI runs the unit tests with Python 3.10 and 3.13, with and without ExifTool,
and installs MagicDispel with the install scripts. Windows and Linux are validated by those
synthetic tests, not by a corpus of real photos.

References: [ExifTool FAQ](https://exiftool.org/faq.html#Q32),
[Apple location metadata guidance](https://support.apple.com/guide/personal-safety/ips0d7a5df82/web).
