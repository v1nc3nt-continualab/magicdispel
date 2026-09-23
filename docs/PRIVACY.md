# Privacy and format limits

MagicDispel removes common capture and descriptive metadata while preserving image encoding,
except BMP is converted losslessly to PNG.
It does not promise forensic anonymization or removal of every metadata byte.

The tool asks ExifTool to delete common metadata, retains color-space/orientation information,
then sanitizes embedded ICC profiles before verification. HEIF editing-only auxiliary
images, thumbnails and URI metadata are removed with dependency/range validation.
This addresses known residues without assuming that ExifTool alone deletes every private structure.

## Information deliberately retained

- ICC color conversion matrices, LUTs and curves. Profiles are rebuilt with a fixed date
  of `2000-01-01 00:00:00`, description `Clean`, and cleared creator, maker/model, CMM,
  platform and old profile ID fields. Calibration dates, descriptive dictionaries and
  unreferenced profile padding are removed. The fixed date is a privacy placeholder.
- Necessary EXIF orientation/color/version fields and normal codec/container information.
- Recognized numeric HEIF XMP fields required for HDR gain maps.
- JPEG gain-map XMP, HDR gain curves/ISO gain-map payloads, and only the numeric
  Apple HDRHeadroom/HDRGain MakerNote fields. These are rendering information.
- HEIF HDR gain maps and alpha images, plus their display dependencies. Recognized depth,
  camera-calibration, semantic/portrait mask, linear-thumbnail and style-delta auxiliaries,
  and `thmb` previews, are removed. Their metadata, actual payload bytes and unreferenced
  properties are erased. Image offsets are stable and shared tiles remain protected.
  URI metadata items, including Apple style property lists, are also removed with their bytes.
- The input filename, with `_clean` and an optional collision suffix appended, by default.
  `--anonymous` instead uses a cryptographically random 128-bit token in the output name.
  It contains no source name, time, MAC address or user ID; it does not modify visible content.

Only individually recognized HEIF HDR XMP fields are permitted. Toolkit strings are
removed from retained HEIF XMP packets. Entire namespaces
are not blindly exempted; unexpected XMP still fails verification. This check is not a proof
that every opaque or private structure contains no personal information.

A previous audit found capture-adjacent ICC dates, identifying profile descriptions and
opaque PLIST data. These known residues are now removed. Recognized legacy Apple `hdgm/gmap`
adaptive curves also have their image-specific 16-byte identifier cleared, while retaining
the numerical curve. Unrecognized ICC tags or adaptive-curve layouts are rejected instead
of being copied unchecked. This has not exhaustively characterized every proprietary format.

Removing auxiliary data reduces later portrait/depth/style editing capabilities. Tested
HEIC and HDR JPEG images retained identical native SDR/HDR pixels and gain-map data.
Old outputs are not retroactively updated: process the original or an old output again.

## File-system information

Outputs are new files populated with the verified main byte stream. Original extended
attributes, macOS resource forks and Windows NTFS alternate streams are not copied.
macOS output xattrs are cleared; Linux user xattrs are cleared where supported.
Normal permissions, directory security attributes, creation/modification times and
OS-generated access-control metadata may exist or be regenerated afterward.

## Image integrity

ExifTool's image-data SHA-256 is compared before and after where available. Pillow checks
all decoded GIF/APNG/AVIF frames and TIFF pages, including animation timing and transparency.
TIFF also verifies its actual encoded strips/tiles and JPEG tables; this works even when
ExifTool cannot calculate ImageDataHash for JPEG-compressed TIFFs.
BMP is decoded and saved as PNG; decoded pixels must match before a result is accepted.
The whole-file hash of the source is also checked to detect concurrent changes.
For HEIF auxiliary removal, retained image item byte ranges must remain exactly equal.
Subsequent ExifTool image/profile checks use the auxiliary-trimmed temporary input;
removed image payloads intentionally no longer match the original. Only color profiles
still used by retained items are preserved and sanitized. Primary/HDR/alpha dependencies
remain; unknown auxiliary types, private layers needed by a retained image, overlapping
payloads, and unsupported references are rejected. Unknown variants can still be refused.
The image-data check is evidence against recompression, not a guarantee that every viewer
will interpret every proprietary format identically. MPF JPEGs are split into constituent
images, each image is cleaned and verified, then a fresh MP index is built. This removes
original MP image identifiers. The compressed image coding bytes, sanitized ICC profiles,
recognized HDR values and numerical gain curves are checked for every constituent image. Invalid indexes
are rejected. Actual iPhone HDR JPEG validation also compared SDR/HDR pixels using macOS ImageIO.

## What this cannot prevent

- Location/identity inference from visible signs, faces, documents, scenery or reflections.
- Matching the image with a previously published or known original image.
- Identification through the account or service used to share the result.
- Information a sharing application adds after cleaning.
- Deliberately hidden information, steganography or forensic sensor attribution.

Use a separate sharing copy and inspect its visible content when anonymity matters.
HDR/alpha layers, codec structure, color curves and original pixel content still provide
possible source/matching clues. A stronger flattened export can sacrifice HDR behavior
and still does not defeat image matching. The current command is not that export mode.

## Validation status

macOS has real-file metadata and native SDR/HDR decoder checks. The repository includes
a pending Windows/Linux/macOS CI matrix for synthetic format, graph and CLI tests.
Windows and Linux have not been executed in this workspace; do not advertise them as
validated until those runs complete. No personal photographs are included in CI fixtures.

References: [ExifTool FAQ](https://exiftool.org/faq.html#Q32),
[Apple location metadata guidance](https://support.apple.com/guide/personal-safety/ips0d7a5df82/web).
