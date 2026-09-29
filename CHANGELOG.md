# Changelog

## Unreleased

An audit of 0.2.2 by a new model read the whole code base and tried to make it misbehave. It
found files refused or saved wrongly, files that took minutes or gigabytes to check, and places
where a file made for it could hide data. None of it leaked what a camera or an app writes.
Upgrading is recommended if you clean BMP or TIFF files, or read-only originals on macOS.

Refused or saved wrongly:

- A read-only original (mode 444 or 400) was refused on macOS with "could not clear extended
  attributes". Attributes are cleared before the mode is set now.
- A 4-bit run-length BMP was saved with wrong pixels, as Pillow decodes it wrongly (macOS does
  not); it is refused now. The profile a version 5 BMP header embeds was dropped, which changes
  the colors in a viewer that applies it; it stays, and one that names a file is refused.
- With ExifTool installed, a TIFF with one of the rarer tags (`NumberofInks`, `DotRange`,
  `TransferRange`, gray response curves and others) was refused, because ExifTool names them
  otherwise than the list did, and a video with a movie box over 32 MiB, a recording of many
  hours, was refused, because ExifTool skips such a box. Both pass now; ExifTool is told to read
  the large movie box.
- On Windows, ExifTool was looked for in the current folder first; only folders of `PATH` given
  in full count now.
- A HEIC of a file type box alone was "cleaned" into a file with no picture, and so was one whose
  only image had no data. Both are refused. A 10-byte `nclx` colour box, as some Android phones
  write it, is accepted, as it is in videos.
- Image sequences keep their extensions: `.heics`, `.heifs` and `.avifs` became `.heic` and
  `.avif`.
- A movie whose media lies in another file (a reference movie) is refused as unsupported, not as
  damaged.
- A file name of more than about 245 characters failed at the end: the name of the copy is cut
  short. A photo is written as `magicdispel-<random>.unfinished` and given its name when whole,
  as videos are, so a crash never leaves half a photo under it.
- 0.2.2 only: a movie box of many boxes took time growing with the square of their number (32,000
  boxes took 25 seconds).

Too slow, or too large, for files made to be:

- GIF: 8 MB of empty comments took two minutes to read; more than a thousand dropped extension
  blocks are refused. An animation whose frames add up to more than four gigapixels (a GIF of
  1.5 KB could ask for 1.1 GB and half a minute) is refused before anything is decoded.
- TIFF: a header of 123 bytes could name billions of strips or of samples per pixel, tags could
  name one range of the file any number of times, and each was copied; ICC profiles likewise (a
  3.7 KB PNG needed 1.1 GB). Ranges are copied once, and counts are checked before any list is
  made.
- JPEG: 16 MB of empty segments took 19 seconds and 600 MB, and 16 MB of fill bytes 16 seconds
  and 900 MB; more than 65,536 segments, or more than 1,024 fill bytes in a row, are refused, and
  an extended XMP packet over 1 MB is not read. EXIF entries that name one range cost nothing
  each. Files of another kind are refused from their first bytes, without being read.
- WebP: libwebp takes time growing with the square of the frames (100,000 frames of one pixel
  took 15 seconds); more than 32,768 are refused. An animation on a canvas of more than 64
  megapixels is refused as well, from its header: Pillow holds several copies of the canvas, and
  a WebP of 1.5 KB could ask for 8 GB and 30 seconds.
- HEIF: a long chain of derived images took minutes (each link searched them all again), so did
  many `ipma` boxes, millions of boxes or extents cost gigabytes, and items naming one large
  range each got a copy of it. Each is now handled in one pass, or refused above 262,144 boxes in
  a container and a million extents or references.
- Video: the check of a movie took time growing with the square of its sample group
  descriptions, and 24 MB of chunk offsets took 1.6 GB; a track of more than about two million
  chunks is refused.

Places where a made-up file could hide data, closed:

- JPEG: quantization and Huffman tables that no scan reads, or that another definition replaces
  first, fill bytes, markers that stand alone, the ignored fields of a sequential scan header,
  and repeated JFIF, EXIF, XMP, ISO gain-map, Apple gain-curve and Adobe segments (a scan reads
  the tables as libjpeg does; the 48 JPEGs of the corpus and 18 made with libjpeg-turbo, FFmpeg
  and macOS, progressive, arithmetic and restart-interval ones among them, come out byte for byte
  as before).
- PNG: a chunk the standard allows once is kept once, and before the image data; the suggested
  palette of a truecolor image goes; image data is cut into chunks of one size and animation
  frames are numbered afresh; sRGB, cICP, pHYs, bKGD and sBIT hold what their standards
  define; an animation holds as many frames as it says.
- GIF: one loop count, one profile, the last graphic control before an image, the image data in
  blocks of 255 bytes, and reserved descriptor bits cleared. WebP: one ANIM, one ICCP, and one
  image with its alpha data in each frame.
- XMP numbers (HDR gain-map fields) are written from their value without an exponent, so their
  digits carry nothing: `1.369850` becomes `1.36985`. XMP in an encoding Python does not know is
  refused, not a crash.
- TIFF: JPEG tables only for JPEG compression, a palette only for palette images, and kept tags
  of the types they call for.
- HEIF: what follows an item's name, and all but the "hidden" flag of an item, are zeroed; `dinf`
  keeps only its `dref`; properties of codecs with no layout here are limited to 2 KB each and
  8 KB in all; an XMP packet too large to read (1 MB) goes with the rest of the XMP.
- Video: the sample dependency table goes from a track of sound, where it could hold anything for
  each sample, a track names each type of reference once, and a track or movie header outside the
  movie box, which some readers take up as an extra stream, is refused.
- The privacy details now say where a made-up file can still put a few kilobits (fields players
  read and the standard leaves free, the order of boxes), and the two that grow with a video:
  how its tables are cut into runs, and the dependency table of its pictures.

Also: libtiff's own messages about a damaged TIFF no longer appear before the command's, 240,000
damaged variants of files of every format and 260,000 with their chunks, segments, blocks and
boxes duplicated, dropped, swapped and retyped were cleaned without a crash, a stall or a copy
that cleans to something else, Pillow 12.3 or newer is required (11.3 has 36 published advisories), each kind of file is
opened by its own decoder only, CI runs Python 3.14 and once with the oldest dependencies allowed,
the regression harness reports an unexpected exception as a problem, not a refusal, and the
documentation is corrected where the audit found claims that were too strong or too weak.
Known limits: a video's metadata box of fewer than 157 bytes, or of 158 to 164, has no room for the
playback intent, which then goes with the rest; a WebP of 268 megapixels takes about 4 GB to
check.

## 0.2.2 (2026-09-26)

A clean copy of an iPhone video of 120 fps or more no longer risks playing in slow motion:
Apple's full frame rate playback intent, the one item of a video's metadata that says how to
play it, is kept. Upgrading is recommended if you clean such videos.

- Videos keep Apple's full frame rate playback intent, which iOS 18 and macOS 15 write to say
  whether a video of 120 fps or more plays at its full rate (1) or in slow motion (0), and which
  some players decide by: without it, a clean copy of an iPhone's 120 fps video could play in
  slow motion. It is the one item of a video's metadata kept. The metadata box is rewritten with
  it alone, as iPhones write it, and the rest of the box is zeroed; a value other than 0 or 1,
  or one stored otherwise, goes with the rest of the metadata.
- A metadata box of more than 256 boxes, keys or items, as one made to exhaust memory would
  hold, is emptied without being read.
- Checked with the three videos from an iPhone 16 on iOS 27 (intents 1, 1 and 0): macOS reads
  the same intent before and after cleaning, and their clean copies differ from 0.2.1's only in
  the metadata box. The regression harness now compares the intent macOS reads.

## 0.2.1 (2026-09-26)

ICC color profiles, in photos and videos alike, keep only what the standard defines of their
header, and only the values the standard lists in their tags; their tags are written in one
order. Before, a profile made for it could carry about 90 bytes through these fields and the
order of its tags. Colors convert exactly as before.

- Of the header: the version's reserved bytes, the reserved and vendor bits of the flags and
  device attributes and the rendering intent's reserved half are zeroed, and the illuminant is
  written as the standard encodes D50. A profile whose classes, spaces, version digits,
  rendering intent or illuminant the standard does not define is refused.
- A technology, image state or gamut signature, or a measurement, viewing conditions,
  chromaticity or cicp enumeration, that is not in the standard's lists gets the profile
  refused.
- Color tags are written in order of their signatures, not in the original's order.
- Adobe RGB, Apple RGB and HP's sRGB profiles, as some images embed them, write a value in the
  rendering intent's reserved half; it is zeroed, and colors convert the same.
- Checked on the 124 distinct profiles found in macOS and the corpus: the same ones are refused
  as before, and colors convert exactly as before, in LittleCMS and in macOS ColorSync, through
  each of the 107 an image can use. The clean copies of 69 corpus samples differ from 0.2.0's
  only in the order of their profiles' tags; the others are identical.

## 0.2.0 (2026-09-25)

Videos: MP4 and QuickTime (MOV) files are cleaned too, without changing a single frame. Tested
on 64 public sample videos, GoPro, iPhone (Dolby Vision, spatial video), Pixel and Samsung ones
among them: 47 clean, FFmpeg decodes identical frames from each, and macOS plays each the same;
the other 17 are refused as designed. Three videos from an iPhone 16 on iOS 27 clean the same
way. So do 394 small files made for the tests with FFmpeg, macOS (avconvert, AVAssetWriter,
ImageIO) and ExifTool, of every codec, container and muxer option they offer, but for those
refused by design (fragmented, audio-only, subtitles, Motion JPEG, MPEG-2, DNxHR, Opus as
AVFoundation writes it into QuickTime, iLBC).

- Kept: video and sound tracks with every sample, decoder configurations, rotation, edit lists,
  color, HDR, Dolby Vision, Apple Log, alpha, Apple's spatial video information and positional
  audio (APAC), and Apple's per-frame scene illuminance, which iPhones mark as used to show
  their HDR video. It is kept only in its exact layout; any other such track is refused.
- Removed: location, device, software and dates in user data and metadata boxes; timed
  metadata tracks (GPS and motion, face detection, Live Photo and motion photo data), timecode
  and chapter tracks, chapter pictures too, with their samples; maker data such as GoPro's
  serial numbers and Samsung's SEF data; creation times, handler, vendor and compressor names;
  brands naming a camera's maker; the extended language tag, which may name a region; unused
  media data and anything after the movie.
- Refused: fragmented, encrypted and audio-only files, subtitle tracks, Google's 360-degree
  videos, codecs and sample entry boxes not on the list, and a removed track that another
  needs to be shown.
- Every kept box has exactly its layout and appears once where the standard allows one, and a
  kept track's sample tables must agree on its samples, chunks and descriptions; no two chunks
  share bytes. Decoder configurations must end where they say (avcC, hvcC, esds, av1C, vpcC,
  dOps, dec3), and FLAC's may hold no tags. Sample groups other than roll distances, sync and
  random access points and temporal layers are emptied, and descriptions no sample uses are
  cleared; so are reserved fields and QuickTime's poster and selection times. Seeking hints no
  player needs (stsh, subs, padb) are emptied. Sound is read by its sample entry, as FFmpeg and
  AVFoundation read it; sound they could read in two ways is refused. The codec maker's name in
  H.263 and AMR configurations is cleared, and FFmpeg's copy of a ProRes encoder's description
  (glbl), compressor name included, is emptied. The gapless playback note (iTunSMPB) goes with
  the metadata.
- A video is cleaned in a copy next to the original, never read into memory: a 4.7 GB video
  takes about five seconds. The copy, named `magicdispel-<random>.unfinished` and the video's
  extension until it is done, is readable only by its owner, and gets the original's permissions
  once it is clean. Ctrl+C, Ctrl+Break, closing the terminal (unless run with `nohup`) and
  stopping MagicDispel remove an unfinished copy. ExifTool's second check reads that copy from
  its path, with the timed metadata in its samples, and gives up after ten minutes.
- A video keeps its extension (.mp4, .mov, .m4v, .3gp, .f4v...), whatever its content: players
  may read one file differently by its extension. `--anonymous` names videos `video_<random>`
  and their extension. A clean copy cleans to itself: cleaning it again changes nothing.
- Photos: a HEIF file keeps only the brands that say how to read it (HEIF's, MIAF's, AVIF's and
  those of the other image codecs); others are cleared, and a second file type box is emptied.
  Item properties whose standards fix their layout (tols, iscl, rloc, amve, a1lx, cclv, colr,
  irot, imir, auxC) must have exactly it, and an image's decoder configuration must end where
  it says. A JPEG image item holding metadata segments (EXIF, comments, ICC), read as a JPEG
  file is, JPEG 2000 image items, a second meta or moov box and image groups naming no image
  are refused. An image sequence's thumbnail track goes, as thumbnail images do, and no removed
  image may share bytes with a kept sequence's frames: ImageIO's animated HEIC with
  thumbnails, refused before, is cleaned. Apple's stereo photos are refused as unsupported, no
  longer as damaged. Checking which removed items share data with kept ones no longer takes
  time that grows with the square of their number. Clean copies of photos also get their
  original's permissions, and names and messages printed never carry a file's control
  characters to the terminal.
- Image sequences (HEIF, AVIF) and videos share one cleaner. The 96 test photos clean exactly as
  before.
- The regression harness checks videos with FFmpeg (every decoded frame and stream) and macOS
  AVFoundation (tracks, rotation, HDR, frames), and cleans every clean copy again.

## 0.1.5 (2026-09-24)

HDR photos taken with iOS 27 are cleaned: 0.1.4 refused them ("unsupported ICC color tag
HAGC"). Tested with an iPhone 16 on iOS 27.0; the clean copy renders identically in macOS, in
SDR and HDR.

- Apple's headroom adaptive gain curve (ICC tag HAGC; SMPTE ST 2094-50 tone-mapping metadata,
  ICC White Paper 62) is kept byte for byte, once every bit of it is checked: reserved bits
  zero, numbers within the standard's ranges, at most four alternate images, nothing after
  the record. It holds only flags, headrooms and curve points, which HDR-aware renderers use
  to show the photo on screens with less headroom.
- iOS 27 moved the fields of Apple's older HDR curve (ICC tag hdgm, type gmap) one byte
  earlier. Both layouts are accepted, for Display P3 and BT.2020 primaries, and the image
  identifier in it is still cleared.

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
