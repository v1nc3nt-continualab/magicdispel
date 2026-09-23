# Privacy and format details

MagicDispel rebuilds each file from an allowlist: it copies only the parts a viewer needs to
show the image and leaves everything else behind. It does not search for known metadata to
delete, so metadata it has never heard of is removed too. It does not promise forensic
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
  fields.
- **Structure.** Transparency, animation frames, timing and loop count, TIFF pages and page
  numbers, and the format's own headers.
- **The file name**, with `_clean` added, unless `--anonymous` replaces it with a random
  128-bit token that contains no name, time, MAC address or user ID.

## What is removed

Everything not listed above, including: EXIF capture time, camera, lens, serial numbers,
location and maker notes; XMP (except gain-map fields); IPTC and Photoshop blocks; comments and
text chunks; C2PA manifests; embedded thumbnails and previews, which may show an uncropped
original; HEIF depth maps, lens calibration, portrait and semantic mattes, style maps, Apple
property lists and item names; JPEG MPF image IDs; image-sequence creation times, handler and encoder names
and user data; TIFF EXIF and GPS directories, descriptions, private tags and sub-images; and
unknown or private data blocks and data after the end of an image.

Removing auxiliary HEIF images limits later portrait, depth-of-field and photographic-style
edits. Tested HEIC and HDR JPEG files render identically on macOS in SDR and HDR.

## How nothing slips through

- **Rebuild, not delete.** Each format has its own rebuilder (`src/magicdispel/formats/`). Parts
  with a fixed size must have exactly that size, so they cannot carry extra bytes. PNG image data
  must inflate to exactly the scanlines the header describes, with nothing after the compressed
  stream. JPEG multi-picture indexes are written fresh.
- **HEIF in place.** HEIF files are cleaned without moving any image data, so every offset
  stays valid: the item tables are rewritten in the space they had, removed items and boxes are
  zero-filled, bytes that no remaining item or sample uses are zeroed, and boxes at the end of
  the file are dropped. This is why HEIC files do not shrink much.
- **Fail closed.** Anything that cannot be handled safely is refused, not passed through: an
  unknown critical PNG chunk, an unknown HEIF item type or auxiliary image, an image that depends
  on a removed layer, fragmented image sequences, media stored outside the file, BigTIFF,
  old-style JPEG in TIFF, metadata inside JPEG-compressed TIFF strips, unrecognized ICC tags.
- **Checked before saving.** The rebuilder parses its own result independently and compares it
  with what the original should yield: for HEIF, for instance, that every retained image item is
  byte-identical, XMP holds only gain-map fields, no editing image, thumbnail or item name
  remains, profiles are sanitized, and unused bytes are zero; for TIFF, that no byte of the file is unaccounted for. Pillow must then decode identical
  pixels, frames, timing and transparency (all formats but HEIC). If ExifTool 12.73+ is
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
gain maps, orientation and DPI), and 11 synthetic leak probes come out clean. A CI matrix for
Windows, Linux and macOS is prepared but has not run; do not treat Windows or Linux as validated
until it has.

References: [ExifTool FAQ](https://exiftool.org/faq.html#Q32),
[Apple location metadata guidance](https://support.apple.com/guide/personal-safety/ips0d7a5df82/web).
