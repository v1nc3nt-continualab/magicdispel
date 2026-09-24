# Privacy and format details

MagicDispel rebuilds each file from an allowlist: it copies only the parts a viewer needs to
show the image or play the video and leaves everything else behind. It does not search for known metadata to
delete, so metadata it has never heard of is removed too, and the parts it keeps must match
their exact layout, so they cannot carry anything else along. It does not promise forensic
anonymization.

## What is kept, and why

- **Image data.** Compressed pixels are copied byte for byte. BMP is the only format that is
  re-encoded, losslessly, as PNG.
- **Color.** ICC profiles keep their color conversion data: matrices, curves, lookup tables,
  Apple's parametric curves in screenshot profiles, HDR adaptive curves (with their
  image-specific identifier cleared), and the headroom adaptive gain curves of HDR photos
  (SMPTE ST 2094-50 tone-mapping data, which holds no identifier). Every profile is rebuilt with the fixed date
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
- **Video.** Video and sound tracks, with every sample copied byte for byte; their decoder
  configurations, rotation and display sizes, edit lists, color and HDR (HDR10, HLG, Dolby
  Vision); Apple's spatial video information: which views there are, the cameras' baseline
  and the projection; and Apple's per-frame scene illuminance, which iPhones mark as used to
  show their HDR video, and which says how bright the scene was, as the video itself shows.
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
encoder names; in videos, the location, device, software and dates of user data and metadata
boxes, timed metadata tracks (GPS and motion data, face detection, Live Photo and motion photo
data, and any other than scene illuminance), timecode and chapter tracks, maker data such as GoPro's serial numbers and Samsung's SEF
data, creation times, handler, vendor and compressor names, and media data no remaining track
uses; TIFF EXIF and GPS directories, descriptions, private tags and sub-images; and unknown or
private data blocks and data after the end of an image or video.

Removing auxiliary HEIF images limits later portrait, depth-of-field and photographic-style
edits. Tested HEIC and HDR JPEG files render identically on macOS in SDR and HDR. Removing a
video's timed metadata likewise ends what only its maker's app draws from it, such as the
pairing of a Live Photo's video with its photo, or Samsung's slow-motion sections. The gapless
playback note some encoders put in the metadata (iTunSMPB) goes too: a player that trims the
encoder's delay by it rather than by the edit list may then play a few milliseconds of
silence at the start of the sound.

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
  the parts of a lookup table, gets the file refused. A headroom adaptive gain curve is read
  bit by bit: a reserved bit that is set, a number outside its range or anything after it gets
  the file refused. ISO 21496-1 gain-map metadata keeps only
  the fields its standard defines, which are all a decoder reads: in JPEG anything after them
  is dropped, and in HEIF, where an item cannot be shortened in place, it gets the file refused.
- **Image sequences.** Animated AVIF and HEIF files keep only the boxes on a fixed list:
  headers, tracks, edits and sample tables, and in each sample entry its decoder
  configuration and color and display properties. Readers skip boxes they do not know, so
  any other box, however it is named, is emptied rather than kept.
- **Videos.** MP4 and QuickTime movies keep only the boxes on a fixed list: headers, tracks,
  edits and sample tables, and in each sample entry its decoder configuration and its color,
  HDR and spatial video boxes. Unlike an image sequence's, a sample entry holding a box not on
  the list is refused rather than emptied, since a video may need it to play. Only video and
  sound tracks stay; the samples of the tracks removed are zeroed with every other byte of the
  media data that no remaining sample uses. Every kept box has exactly the layout its type and
  version give it, and appears once where the standard allows one; a kept track's sample
  tables must agree on its samples, chunks and sample descriptions, and no two chunks may share
  bytes, so that players read the samples MagicDispel keeps and nothing else. A decoder
  configuration must end where it says it does (avcC, hvcC, lhvC, esds, av1C, vpcC, dOps);
  FLAC's may hold only its stream information, not tags or pictures. Sample groups stay only
  for roll distances, sync and random access points and temporal layers, and their
  descriptions that no sample uses are cleared; others, which only help seeking, are emptied,
  as are shadow sync, sub-sample and padding tables. Reserved fields, QuickTime's preview,
  poster and selection times, and a visual entry's data size are cleared. The file type box
  keeps the brands that say how to read the file, and its minor version, a number some
  encoders set; brands such as those naming a camera's maker are cleared. The name of the
  codec's maker in H.263 and AMR configurations is cleared, and so is the extended language
  tag, which may name a region. Uncompressed sound is read by its sample entry, as players
  read it; sound they could read in two ways is refused. The check compares every box of the
  result with the original's and every kept sample byte for byte. A video is cleaned in a copy
  next to the original, and neither is read into memory. FFmpeg's copy of a ProRes encoder's
  description (glbl) is emptied, as its compressor name would be. Until it is clean, the copy is named
  `magicdispel-<random>.unfinished` and readable only by its owner; it then gets the original's
  permissions.
- **HEIF and videos in place.** HEIF files and videos are cleaned without moving any image or
  media data, so every offset stays valid: the item tables are rewritten in the space they had,
  removed items and boxes are zero-filled, bytes that no remaining item or sample uses are
  zeroed, and boxes at the end of the file are dropped. This is why HEIC files and videos do not
  shrink much. An emptied box keeps its size, which shows how much metadata there was, though
  not what it said.
- **Fail closed.** Anything that cannot be handled safely is refused, not passed through: an
  unknown critical PNG chunk, an unknown HEIF item type or auxiliary image, a HEIF property of
  unknown meaning that readers may not ignore, an image that depends on a removed layer, image
  groups other than alternatives (such as the stereo pairs of spatial photos), JPEG
  multi-picture images other than previews and gain maps (such as stereo pairs), sequence
  tracks other than pictures and their alpha, fragmented image sequences and videos, encrypted
  and audio-only videos, video tracks other than video, sound, timed metadata, timecode and
  chapters (subtitles, for instance), video codecs and sample entry boxes not on the list
  (Motion JPEG among them), Google's 360-degree videos, sample tables that disagree, decoder
  configurations with data after them, uncompressed sound players could read in two ways, file
  types of unknown major brands, a removed track that another needs to be shown, media stored
  outside the file, BigTIFF, old-style JPEG in TIFF, metadata inside JPEG-compressed TIFF strips,
  unrecognized ICC tags and floating-point ICC transforms, gain-map metadata of an unknown
  version. RAW photos built on TIFF (DNG, CR2, NEF and others) are refused too: their TIFF
  pages hold only a preview.
- **Checked before saving.** The rebuilder parses its own result independently and compares it
  with what the original should yield: for HEIF, for instance, that every retained image item is
  byte-identical, XMP holds only gain-map fields, no editing image, thumbnail or item name
  remains, profiles are sanitized, and unused bytes are zero; for TIFF, that no byte of the
  file is unaccounted for. Pillow must then decode identical pixels, frames, timing and
  transparency (all formats but HEIC and videos, which are compared byte for byte; for JPEG,
  of the images kept). If ExifTool 12.73+ is
  installed, it reads the result as a second opinion, and any warning, private field or data it
  cannot identify stops the save. The original's hash is compared before and after, so a file
  changed by another program during cleaning is not published.

## Out of scope

Data hidden inside the compressed image data itself, for example in JPEG scans, VP8 or HEVC
frames, GIF LZW data or unused palette entries, is copied along with the image. So is data
inside video and sound samples, such as the SEI messages some encoders write into H.264 and
HEVC frames: the frames of an iPhone's Live Photo video, for one, carry an 8-byte value of
unknown meaning that other videos lack. Decoder configurations (such as hvcC and avcC) are
copied whole too, as the samples are, up to their end; those of other codecs (VVC, APV, AC-4,
MPEG-H, DTS and more) are not read at all. So are fields that players read and whose values a
made-up file could choose freely: track IDs, display sizes and resolutions, the graphics mode,
the composition offsets of cslg, roll distances, and QuickTime's quality and revision fields in
sample entries, which macOS reads. Apple's positional audio configuration (dapa) is copied
whole. Detecting such steganography is beyond this tool.

If MagicDispel is killed, or the drive a video is on goes away while it is being cleaned, the
unfinished copy may stay next to it, named `magicdispel-<random>.unfinished`: it may still hold
everything the original does, and can be deleted. Closing the console window on Windows kills
MagicDispel the same way.

A JPEG gain map that only an Ultra HDR GContainer directory points to, with no multi-picture
index, is not recognized: it sits after the end of the image and is removed as trailing data,
so such a photo keeps its SDR look but loses its HDR one. Ultra HDR photos from Android carry a
multi-picture index and keep their gain maps.

Some phones and cameras append their own data after the image, which is removed the same way:
Huawei's HDR Vivid image and editing history, OnePlus and OPPO's local HDR masks, Honor's
extended information, and previews that no index lists, from cameras such as Leica and OM
System. Other apps do not read this data, so the photos look the same in them; the maker's own
gallery app may no longer show its HDR effect.

## File-system information

Outputs are new files containing only the verified bytes. Extended attributes, macOS resource
forks and Windows alternate data streams of the original are not copied; macOS output
attributes are cleared, as are Linux `user.` attributes where supported. macOS may then add its
own bookkeeping attributes, such as `com.apple.provenance` and `com.apple.macl`, which record
which programs created or opened the file and hold nothing about the photo. Normal permissions
and creation/modification times of the new file are set by the system.

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
before.

Since 0.1.4 the corpus also holds 27 photos straight from, or exported from, 21 current phones
and cameras, taken from Wikimedia Commons: Samsung Galaxy S25 Ultra and S24, Google Pixel 8a
and 9 Pro, Xiaomi 14T Pro and 15 Ultra, Huawei Mate 60 Pro and Pura 70 Ultra, Honor Magic6 Pro,
vivo X200 Pro, OnePlus 13, OPPO Find X3 Pro, Canon EOS R5 and R6 Mark II, Nikon Z 6II and Z 8,
Sony α7 IV, Fujifilm X100V, Panasonic S5II, OM System OM-1 and Leica Q2. It also holds Google's
Ultra HDR samples from libultrahdr and Skia. All of them clean and render identically in macOS,
in SDR and HDR, and no private field survives of the 5 to 90 each photo carried.

Since 0.2.0 the corpus also holds 64 public sample videos: GoPro's HERO5, HERO7, HERO8,
Fusion, MAX and Karma samples; AndroidX Media's test files, among them an iPhone 14 Pro Dolby
Vision video, an Apple spatial video, Pixel motion photo and HLG videos and Samsung
slow-motion videos; and ExifTool's. 47 clean: FFmpeg decodes identical frames from each, macOS
AVFoundation sees the same video and sound tracks and shows the same frames, and ExifTool finds
no private field. The other 17 are refused as designed: audio-only, fragmented and encrypted
files, subtitles, two damaged files, Motion JPEG and a track needed to show the video. Three
videos from an iPhone 16 on iOS 27 (a Live Photo's video, an HDR video and an H.264 one)
clean the same way, keeping their scene illuminance. A 4.7 GB video is cleaned in under five
seconds, with 24 MB of memory.

On macOS, Windows and Linux, CI runs the unit tests with Python 3.10 and 3.13, with and without
ExifTool, and installs MagicDispel with the install scripts. On Windows and Linux, MagicDispel
is validated by those synthetic tests, not by a corpus of real photos.

References: [ExifTool FAQ](https://exiftool.org/faq.html#Q32),
[Apple location metadata guidance](https://support.apple.com/guide/personal-safety/ips0d7a5df82/web).
