#!/usr/bin/env python3
"""Run MagicDispel over a local sample corpus and check that nothing regressed.

A corpus is a folder of images plus manifest.json: [{"id", "label", "file",
"name"}, ...], created from the folder's images on first use. Keep it outside
the repository; it usually holds personal photos.

    python scripts/regression.py CORPUS
    python scripts/regression.py CORPUS --baseline CORPUS/runs/<run>.json
    python scripts/regression.py CORPUS --baseline <run>.json --identical

Every run compares each cleaned output with its own input. Pillow must decode
the same frames (a JPEG may leave out pictures after its first, such as
previews); on macOS, ImageIO/ColorSync must render the same pixels, HDR,
gain maps, orientation and DPI; a video must keep its video and sound tracks
as AVFoundation sees them, and any other track it keeps unchanged, and ffmpeg
must decode the same frames and streams; the source must be untouched; and no probe marker (see
make_probes.py) may survive. With --baseline, every sample must
also keep its outcome, and outputs must not gain metadata tags the baseline
output lacked. --identical additionally demands byte-identical outputs: the
bar for pure refactoring. Each run is saved to CORPUS/runs/ for later diffs.
"""
import argparse
import collections
import concurrent.futures
import datetime
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image

from magicdispel import core
from magicdispel.exiftool import find as find_exiftool, usable, version

MARKER = b"SECRET-40.7128N"
HELPER = Path(__file__).with_name("native_render.swift")
VIDEO_HELPER = Path(__file__).with_name("native_video.swift")
CACHE_VERSION = 5
VIDEO_SUFFIXES = {".mp4", ".m4v", ".mov", ".qt", ".3gp", ".3g2"}
# Sample entry atoms whose first bytes name the codec's maker, cleared: H.263 and AMR.
VENDOR_ATOMS = {"d263", "damr"}
# ffprobe's properties of a video or sound stream that say how it plays.
STREAM_FIELDS = ("codec_type", "codec_name", "codec_tag_string", "profile", "width", "height", "pix_fmt",
                 "color_range", "color_space", "color_transfer", "color_primaries", "field_order",
                 "r_frame_rate", "avg_frame_rate", "nb_frames", "duration_ts", "time_base", "start_pts",
                 "sample_rate", "channels", "channel_layout", "bits_per_sample", "sample_aspect_ratio",
                 "display_aspect_ratio", "side_data_list")
# Display fields an output may newly keep, provided the value is the input's own.
DISPLAY_TAGS = re.compile(
    r"^(Orientation|[XY]Resolution|ResolutionUnit|PixelsPerUnit[XY]|PixelUnits|SRGBRendering|"
    r"Gamma|WhitePoint[XY]|(Red|Green|Blue)[XY]|ColorSpace|InteropIndex|BackgroundColor|"
    r"ColorPrimaries|TransferCharacteristics|MatrixCoefficients|VideoFullRangeFlag|JFIFVersion)$")
# Values describing the file's own layout (positions, sizes, which optional parts
# are present, and so whether ExifTool calls it e.g. "Extended WEBP"; emptied
# free space), which change whenever other parts are dropped or emptied. They
# may appear, disappear or change without counting as metadata.
LAYOUT_TAGS = {"MPImageStart", "MPImageLength", "StripOffsets", "TileOffsets", "WebP_Flags", "FileType",
               "MediaDataOffset", "MediaDataSize", "MediaData", "Free", "Unknown_free",
               # ExifTool's names for the strip offsets of JPEG-compressed TIFF
               "PreviewImageStart", "PreviewImageLength"}


# ---------------------------------------------------------------- fingerprints

def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def pillow_fingerprint(path):
    """A hash of each decoded frame with its timing, or None if Pillow can't decode it."""
    try:
        with Image.open(path) as image:
            frames = []
            for index in range(getattr(image, "n_frames", 1)):
                image.seek(index)
                frame = image.convert("RGBA")
                digest = hashlib.sha256(repr((frame.size, image.info.get("duration"))).encode())
                digest.update(frame.tobytes())
                frames.append(digest.hexdigest())
            return frames
    except (OSError, ValueError):
        return None


def is_video(path):
    return Path(path).suffix.lower() in VIDEO_SUFFIXES


def decoded_fingerprint(path):
    """What ffmpeg, a decoder independent of macOS, makes of a video: a hash of
    every decoded video and sound frame, and each such stream's properties;
    None for other files, or without ffmpeg."""
    if not is_video(path) or not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        return None
    decoded = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v?", "-map", "0:a?",
                              "-f", "framemd5", "-"], capture_output=True)
    lines = [line for line in decoded.stdout.decode("utf-8", "replace").splitlines()
             if line and not line.startswith("#")]
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)], capture_output=True)
    streams = json.loads(probe.stdout or b"{}").get("streams", [])
    return {"frames": None if decoded.returncode else hashlib.sha256("\n".join(lines).encode()).hexdigest(),
            "count": len(lines), "streams": [{field: stream.get(field) for field in STREAM_FIELDS}
                                             for stream in streams if stream.get("codec_type") in ("video", "audio")]}


class NativeRenderer:
    """macOS fingerprints: of images from ImageIO/ColorSync, via the compiled
    native_render.swift, and of videos from AVFoundation, via native_video.swift."""

    def __init__(self, cache):
        self.binaries = {}
        if sys.platform != "darwin" or not shutil.which("swiftc"):
            return
        for video, helper in ((False, HELPER), (True, VIDEO_HELPER)):
            version = hashlib.sha256(helper.read_bytes()).hexdigest()[:12]
            binary = cache / ("%s-%s" % (helper.stem, version))
            if not binary.exists():
                cache.mkdir(parents=True, exist_ok=True)
                subprocess.run(["swiftc", "-O", "-module-cache-path", str(cache / "swift-modules"),
                                str(helper), "-o", str(binary)], check=True)
            self.binaries[video] = binary

    @property
    def version(self):
        return "+".join(binary.name for binary in self.binaries.values()) or None

    def fingerprints(self, paths):
        """Map str(path) -> fingerprint. Small batches keep memory use bounded."""
        result = {}
        for video, binary in self.binaries.items():
            chosen = [str(path) for path in paths if is_video(path) == video]
            for start in range(0, len(chosen), 6):
                lines = subprocess.run([str(binary), *chosen[start:start + 6]], capture_output=True, text=True,
                                       check=True).stdout.splitlines()
                for line in lines:
                    record = json.loads(line)
                    result[record.pop("file")] = record
        return result


def exiftool_tags(exiftool, paths):
    """Every tag ExifTool reports for each file, excluding file-system details;
    for videos, the timed metadata in their samples too."""
    result = {}
    for video in (False, True):
        chosen = [str(path) for path in paths if is_video(path) == video]
        if not chosen:
            continue
        output = subprocess.run(
            [exiftool, "-config", "", "-charset", "filename=UTF8", "-j", "-G1:4", "-a", "-s",
             "-u", "-e", "-n", *(["-ee", "-api", "LargeFileSupport=1"] if video else []), *chosen],
            capture_output=True).stdout
        for entry in json.loads(output or b"[]"):
            source = entry.pop("SourceFile")
            result[source] = {key: shorten(value) for key, value in entry.items()
                              if key.split(":")[0] not in {"System", "ExifTool"}}
    return result


def shorten(value):
    text = value if isinstance(value, str) else json.dumps(value)
    return text if len(text) <= 120 else text[:117] + "..."


# ------------------------------------------------------------------ structure

def structure(path):
    """A readable list of the container's top-level parts, for reviewing diffs."""
    data = Path(path).read_bytes()
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        parts = png_parts(data)
    elif data.startswith(b"\xff\xd8"):
        parts = jpeg_parts(data)
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        parts = riff_parts(data)
    elif data[:6] in {b"GIF87a", b"GIF89a"}:
        parts = gif_parts(data)
    elif data[4:8] == b"ftyp":
        parts = bmff_parts(data)
    elif data[:4] in {b"II*\0", b"MM\0*"}:
        parts = tiff_parts(data)
    else:
        parts = ["unrecognized"]
    return collapse(parts)


def collapse(parts):
    runs = []
    for part in parts:
        if runs and runs[-1][0] == part:
            runs[-1][1] += 1
        else:
            runs.append([part, 1])
    return [part if count == 1 else "%s x%d" % (part, count) for part, count in runs]


def png_parts(data):
    parts, p = [], 8
    while p + 8 <= len(data):
        size, kind = struct.unpack_from(">I4s", data, p)
        parts.append(kind.decode("latin-1"))
        p += 12 + size
    return parts


def jpeg_parts(data):
    parts, p = ["SOI"], 2
    while p + 2 <= len(data):
        if data[p] != 0xFF:
            break
        marker = data[p + 1]
        if marker == 0xFF:
            p += 1
            continue
        if marker == 0xD9:
            parts.append("EOI")
            p += 2
            break
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            p += 2
            continue
        if p + 4 > len(data):
            break
        length = int.from_bytes(data[p + 2:p + 4], "big")
        if 0xE0 <= marker <= 0xEF:
            payload = data[p + 4:p + 4 + min(length - 2, 32)]
            name = payload.split(b"\0", 1)[0][:16].decode("latin-1", "replace")
            parts.append("APP%d:%s" % (marker - 0xE0, name))
        else:
            parts.append({0xDB: "DQT", 0xC4: "DHT", 0xDD: "DRI", 0xDA: "SOS", 0xFE: "COM"}
                         .get(marker, "SOF%d" % (marker - 0xC0) if 0xC0 <= marker <= 0xCF else "FF%02X" % marker))
        p += 2 + length
        if marker == 0xDA:
            p = entropy_end(data, p)
    if p < len(data):
        parts.append("after EOI: %d bytes" % (len(data) - p))
    return parts


def entropy_end(data, p):
    """Skip compressed scan data: stop at the first FF that starts a real marker."""
    while True:
        p = data.find(b"\xff", p)
        if p < 0 or p + 1 >= len(data):
            return len(data)
        following = data[p + 1]
        if following in {0x00, 0xFF} or 0xD0 <= following <= 0xD7:
            p += 1
            continue
        return p


def riff_parts(data):
    parts, p = [], 12
    while p + 8 <= len(data):
        kind, size = struct.unpack_from("<4sI", data, p)
        parts.append(kind.decode("latin-1"))
        p += 8 + size + (size & 1)
    return parts


def gif_parts(data):
    def after_blocks(p):
        while p < len(data) and data[p]:
            p += data[p] + 1
        return p + 1

    parts = []
    p = 13 + (3 << ((data[10] & 7) + 1) if data[10] & 0x80 else 0)
    while p < len(data):
        tag = data[p]
        if tag == 0x3B:
            parts.append("trailer")
            p += 1
            break
        if tag == 0x2C:
            flags = data[p + 9]
            p = after_blocks(p + 11 + (3 << ((flags & 7) + 1) if flags & 0x80 else 0))
            parts.append("image")
        elif tag == 0x21:
            label = data[p + 1]
            if label == 0xFF:
                parts.append("app:" + data[p + 3:p + 3 + data[p + 2]].decode("latin-1"))
            else:
                parts.append({0xF9: "gce", 0xFE: "comment", 0x01: "plain-text"}.get(label, "ext%02X" % label))
            p = after_blocks(p + 2)
        else:
            parts.append("unrecognized")
            break
    if p < len(data):
        parts.append("after trailer: %d bytes" % (len(data) - p))
    return parts


def bmff_boxes(data, start, end):
    while start + 8 <= end:
        size, kind = struct.unpack_from(">I4s", data, start)
        header = 8
        if size == 1:
            size, header = struct.unpack_from(">Q", data, start + 8)[0], 16
        elif size == 0:
            size = end - start
        if size < header or start + size > end:
            return
        yield kind.decode("latin-1"), start + header, start + size
        start += size


def bmff_parts(data):
    parts = []
    for kind, content, end in bmff_boxes(data, 0, len(data)):
        parts.append(kind)
        if kind != "meta":
            continue
        for child, body, child_end in bmff_boxes(data, content + 4, end):
            parts.append("meta/" + child)
            if child == "iinf":
                wide = data[body] != 0
                entries = body + (8 if wide else 6)
                for _, info, _ in bmff_boxes(data, entries, child_end):
                    width = 2 if data[info] == 2 else 4
                    parts.append("item:" + data[info + 6 + width:info + 10 + width].decode("latin-1"))
            elif child == "iprp":
                for container, props, props_end in bmff_boxes(data, body, child_end):
                    if container == "ipco":
                        for prop, value, value_end in bmff_boxes(data, props, props_end):
                            if prop == "auxC":
                                urn = data[value + 4:value_end].split(b"\0", 1)[0]
                                parts.append("aux:" + urn.decode("latin-1"))
    return parts


def tiff_parts(data):
    order = "<" if data[:2] == b"II" else ">"
    parts, seen = [], set()
    offset = struct.unpack_from(order + "I", data, 4)[0]
    while offset and offset not in seen and offset + 2 <= len(data):
        seen.add(offset)
        count = struct.unpack_from(order + "H", data, offset)[0]
        tags = [struct.unpack_from(order + "H", data, offset + 2 + 12 * i)[0] for i in range(count)]
        parts.append("IFD%d:%s" % (len(seen) - 1, ",".join(map(str, tags))))
        next_offset = offset + 2 + 12 * count
        offset = struct.unpack_from(order + "I", data, next_offset)[0] if next_offset + 4 <= len(data) else 0
    return parts


# -------------------------------------------------------------------- running

def clean_sample(exiftool, corpus, sample, workdir):
    folder = workdir / sample["id"]
    folder.mkdir()
    source = folder / sample["name"]
    shutil.copyfile(corpus / sample["file"], source)
    started = time.perf_counter()
    record = {"id": sample["id"], "label": sample["label"], "input_suffix": source.suffix}
    try:
        output = core.clean(str(source), exiftool)
    except (OSError, ValueError) as error:
        record.update(status="refused", message=str(error))
    else:
        record.update(status="cleaned", output=str(output))
    record["seconds"] = round(time.perf_counter() - started, 2)
    record["source_unchanged"] = sha256_file(source) == sha256_file(corpus / sample["file"])
    record["leftovers"] = sorted(path.name for path in folder.iterdir()
                                 if path != source and str(path) != record.get("output"))
    return record


def complete(native):
    """Whether a cached macOS fingerprint holds every result and may be reused.
    One taken where the renderer failed (in a sandbox, say) is taken again."""
    if native is None:  # no renderer on this system
        return True
    fields = ("srgb",) if "tracks" in native else ("raw", "srgb", "p3")  # a video's fingerprint has tracks
    return (not native.get("error") and native.get("hdr", "") is not None
            and all(not frame.get("error") and None not in [frame.get(field) for field in fields]
                    for frame in native.get("frames", [])))


def input_fingerprints(corpus, samples, renderer, exiftool, workers):
    """Inputs never change, so their fingerprints are cached by content hash."""
    cache_path = corpus / ".cache" / "inputs.json"
    try:
        cache = json.loads(cache_path.read_text())
    except (OSError, ValueError):
        cache = {}
    key = "%d/%s" % (CACHE_VERSION, renderer.version)
    if cache.get("key") != key:
        cache = {"key": key, "files": {}}
    paths = {sample["id"]: corpus / sample["file"] for sample in samples}
    hashes = {ident: sha256_file(path) for ident, path in paths.items()}
    missing = [ident for ident in paths if hashes[ident] not in cache["files"]
               or not complete(cache["files"][hashes[ident]]["native"])]
    if missing:
        with concurrent.futures.ThreadPoolExecutor(workers) as pool:
            pillow = dict(zip(missing, pool.map(lambda i: pillow_fingerprint(paths[i]), missing)))
            decoded = dict(zip(missing, pool.map(lambda i: decoded_fingerprint(paths[i]), missing)))
        native = renderer.fingerprints([paths[ident] for ident in missing])
        tags = exiftool_tags(exiftool, [paths[ident] for ident in missing])
        for ident in missing:
            cache["files"][hashes[ident]] = {
                "structure": structure(paths[ident]),
                "pillow": pillow[ident],
                "decoded": decoded[ident],
                "native": native.get(str(paths[ident])),
                "tags": tags.get(str(paths[ident]), {}),
                "probe": MARKER in paths[ident].read_bytes(),
            }
        cache_path.parent.mkdir(exist_ok=True)
        cache_path.write_text(json.dumps(cache))
    return {ident: dict(cache["files"][hashes[ident]], sha256=hashes[ident]) for ident in paths}


MEDIA_SUFFIXES = {".jpg", ".jpeg", ".jpe", ".png", ".apng", ".heic", ".heif", ".hif", ".avif",
                  ".webp", ".gif", ".tif", ".tiff", ".bmp"} | VIDEO_SUFFIXES


def load_manifest(corpus):
    """Read manifest.json, creating it from the folder's images on first use."""
    path = corpus / "manifest.json"
    if not path.exists():
        images = sorted(p.name for p in corpus.iterdir() if p.suffix.lower() in MEDIA_SUFFIXES)
        entries = [{"id": "F%02d" % n, "label": name, "file": name, "name": name}
                   for n, name in enumerate(images, 1)]
        path.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n")
    return json.loads(path.read_text())


def run(corpus, workers, keep, without_exiftool=False):
    samples = load_manifest(corpus)
    exiftool = find_exiftool()
    if not exiftool:
        sys.exit("The harness itself reads metadata with ExifTool; please install it.")
    if not usable(version(exiftool)):
        sys.exit("The harness needs ExifTool 12.73 or newer.")
    cleaner_exiftool = None if without_exiftool else exiftool
    renderer = NativeRenderer(corpus / ".cache")
    inputs = input_fingerprints(corpus, samples, renderer, exiftool, workers)
    workdir = Path(tempfile.mkdtemp(prefix="regression-", dir=corpus / ".cache"))
    try:
        with concurrent.futures.ThreadPoolExecutor(workers) as pool:
            records = list(pool.map(lambda s: clean_sample(cleaner_exiftool, corpus, s, workdir), samples))
        outputs = [Path(r["output"]) for r in records if r["status"] == "cleaned"]
        native = renderer.fingerprints(outputs)
        tags = exiftool_tags(exiftool, outputs)
        with concurrent.futures.ThreadPoolExecutor(workers) as pool:
            pillow = dict(zip(outputs, pool.map(pillow_fingerprint, outputs)))
            decoded = dict(zip(outputs, pool.map(decoded_fingerprint, outputs)))
        for record in records:
            record["input"] = inputs[record["id"]]
            if record["status"] != "cleaned":
                continue
            path = Path(record.pop("output"))
            data = path.read_bytes()
            record["output"] = {
                "suffix": path.suffix,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
                "structure": structure(path),
                "pillow": pillow[path],
                "decoded": decoded[path],
                "native": native.get(str(path)),
                "tags": tags.get(str(path), {}),
                "marker": MARKER in data,
            }
        if keep:
            shutil.copytree(workdir, keep)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return records


# ------------------------------------------------------------------- checking

def check_against_input(record):
    """Problems visible without any baseline: the output must look like its input."""
    problems = []
    if not record["source_unchanged"]:
        problems.append("source file was modified")
    if record["leftovers"]:
        problems.append("left files behind: " + ", ".join(record["leftovers"]))
    if record["status"] != "cleaned":
        return problems
    source, output = record["input"], record["output"]
    if output["marker"]:
        problems.append("LEAK: probe marker survived cleaning")
    jpeg = output["suffix"].lower() in {".jpg", ".jpeg", ".jpe"}
    if source["pillow"] and not same_frames(source["pillow"], output["pillow"], jpeg):
        problems.append("Pillow decodes different pixels or timing")
    problems.extend(decoded_differences(source.get("decoded"), output.get("decoded")))
    before, after = source["native"], output["native"]
    if (before is None) != (after is None):
        problems.append("no macOS fingerprint of the " + ("input" if before is None else "output"))
    elif before and after and output["suffix"].lower() in VIDEO_SUFFIXES:
        problems.extend(native_video_differences(before, after))
    elif before and after:
        # BMP becomes PNG: the decoder's raw layout may differ, rendered pixels may not.
        converted = record["input_suffix"].lower() == ".bmp"
        problems.extend(native_differences(before, after, converted))
    return problems


def decoded_differences(before, after):
    """A video must decode, in ffmpeg, to the same frames and streams. An input
    ffmpeg cannot decode proves nothing either way, and is left to macOS."""
    if not before or before["frames"] is None:
        return []
    problems = []
    if not after or after["frames"] != before["frames"]:
        problems.append("ffmpeg decodes different frames")
    if not after or after["streams"] != before["streams"]:
        problems.append("ffprobe sees different streams")
    return problems


def native_video_differences(before, after):
    """A video must keep, in macOS, its video and sound tracks as they were;
    any other track it keeps (Apple's scene illuminance) must be one of the
    input's, unchanged; and frames macOS shows of the input must look the same.
    Frames of codecs macOS does not decode are left to ffmpeg."""
    if before.get("error"):
        return []
    if after.get("error"):
        return ["macOS cannot open the output"]

    def tracks(native):
        return [comparable(track) for track in native.get("tracks", [])]

    def playing(native):
        return [track for track in tracks(native) if track["type"] in ("vide", "soun")]

    problems = []
    if playing(before) != playing(after):
        problems.append("macOS sees different video or sound tracks")
    if any(track not in tracks(before) for track in tracks(after)):
        problems.append("output has a track that differs from the input's")
    if before.get("duration") != after.get("duration"):
        problems.append("macOS duration %s -> %s" % (before.get("duration"), after.get("duration")))
    if any(a != b for a, b in zip(before.get("frames", []), after.get("frames", [])) if a.get("srgb")):
        problems.append("macOS shows different frames")
    return problems


def comparable(track):
    """A track as macOS describes it, less the sample entry atoms that start
    with the name of the codec's maker, which MagicDispel clears."""
    formats = []
    for found in track.get("formats", []):
        extensions = found.get("extensions", {})
        atoms = extensions.get("SampleDescriptionExtensionAtoms")
        if isinstance(atoms, dict):
            atoms = {kind: value for kind, value in atoms.items() if kind not in VENDOR_ATOMS}
            found = {**found, "extensions": {**extensions, "SampleDescriptionExtensionAtoms": atoms}}
        formats.append(found)
    return {**track, "formats": formats}


def same_frames(before, after, jpeg):
    """Whether Pillow decodes the same frames from an output as from its input.
    A JPEG's output may leave out pictures after the first: previews are dropped,
    and Pillow shows an Ultra HDR photo with or without its gain map, depending
    on how its XMP is written. The macOS check compares the gain maps."""
    if not jpeg or not after:
        return after == before
    remaining = iter(before[1:])
    return after[0] == before[0] and all(frame in remaining for frame in after[1:])


def native_differences(before, after, converted):
    """Differences between the macOS fingerprints of an input and its output.
    A result missing on either side is a problem too: two failed decodes
    compare equal, but prove nothing."""
    problems = ["macOS cannot open the %s" % side for side, native in (("input", before), ("output", after))
                if native.get("error")]
    if before.get("count") != after.get("count"):
        problems.append("macOS sees %s images instead of %s" % (after.get("count"), before.get("count")))
    for index, (a, b) in enumerate(zip(before.get("frames", []), after.get("frames", []))):
        fields = ["size", "orientation", "srgb", "p3"] + ([] if converted else ["raw"])
        missing = [field for field in fields if a.get(field) is None or b.get(field) is None]
        if a.get("error") or b.get("error") or missing:
            problems.append("frame %d not verified in macOS: %s" % (index, ", ".join(missing) or "cannot decode"))
            continue
        changed = [field for field in fields if a.get(field) != b.get(field)]
        if display_dpi(a) != display_dpi(b):
            changed.append("DPI %s -> %s" % (display_dpi(a), display_dpi(b)))
        if changed:
            problems.append("frame %d differs in macOS: %s" % (index, ", ".join(changed)))
    for field in ("hdr", "apple_gain_map", "iso_gain_map"):
        if field not in before:
            continue
        if before[field] is None or (field == "hdr" and after.get(field) is None):
            problems.append("macOS %s not verified" % field.replace("_", " "))
        elif before[field] != after.get(field):
            problems.append("macOS %s differs" % field.replace("_", " "))
    added = set(after.get("auxiliaries", [])) - set(before.get("auxiliaries", []))
    if added:
        problems.append("output gained auxiliary images: " + ", ".join(sorted(added)))
    return problems


def display_dpi(frame):
    """The resolution apps size the image by: missing, or a JFIF aspect ratio
    without units (reported as 1), both mean the 72 DPI default."""
    return tuple(72 if value is None or value <= 1 else round(value, 2)
                 for value in frame.get("dpi") or (None, None))


def check_against_baseline(record, old, identical, expected_changes):
    problems, notes = [], []
    if old is None:
        return problems, ["new sample"]
    if record["status"] != old["status"] and record["id"] not in expected_changes:
        problems.append("outcome changed: %s -> %s" % (old["status"], record["status"]))
    if record["status"] != "cleaned" or old["status"] != "cleaned":
        if record.get("message") != old.get("message"):
            notes.append("message changed")
        return problems, notes
    new, previous = record["output"], old["output"]
    now, then, source = tag_items(new["tags"]), tag_items(previous["tags"]), tag_items(record["input"]["tags"])
    # BMP fields have other names in PNG; the macOS check compares their effect.
    converted = record["input_suffix"].lower() == ".bmp"

    def kept_display_field(item):
        return DISPLAY_TAGS.match(item[0].split(":")[-1]) and (converted or source[item])

    gained = now - then
    restored = sorted(key for key, value in gained.elements() if kept_display_field((key, value)))
    unexpected = sorted("%s=%s" % item for item in gained.elements() if not kept_display_field(item))
    if unexpected:
        problems.append("new or changed metadata: " + ", ".join(unexpected))
    if restored:
        notes.append("display fields kept from the input: " + ", ".join(restored))
    lost = sorted(key for key, _ in (then - now).elements())
    if lost:
        notes.append("tags removed or changed: " + ", ".join(lost))
    if new["structure"] != previous["structure"]:
        notes.append("structure: %s -> %s" % (" ".join(previous["structure"]), " ".join(new["structure"])))
    if new["sha256"] != previous["sha256"]:
        (problems if identical else notes).append("output bytes differ")
    return problems, notes


def tag_items(tags):
    """(tag, value) pairs as a multiset, leaving out layout tags. ExifTool
    numbers duplicates Copy1, Copy2...; removing one renumbers the rest, so the
    numbers are dropped."""
    items = collections.Counter()
    for key, value in tags.items():
        name = ":".join(part for part in key.split(":") if not part.startswith("Copy"))
        if name.split(":")[-1] not in LAYOUT_TAGS:
            items[name, value] += 1
    return items


def git_label(repository):
    try:
        return subprocess.run(["git", "describe", "--always", "--dirty"], cwd=repository,
                              capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("--baseline", type=Path, help="earlier run to compare against")
    parser.add_argument("--identical", action="store_true", help="require byte-identical outputs")
    parser.add_argument("--expect-change", nargs="*", default=[], metavar="ID",
                        help="samples whose outcome is allowed to change")
    parser.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1))
    parser.add_argument("--keep", type=Path, help="copy cleaned outputs to this new folder")
    parser.add_argument("--without-exiftool", action="store_true",
                        help="clean without ExifTool's second check, as for users who lack it")
    args = parser.parse_args()

    corpus = args.corpus.resolve()
    records = run(corpus, args.workers, args.keep, args.without_exiftool)
    baseline ={r["id"]: r for r in json.loads(args.baseline.read_text())["records"]} if args.baseline else {}

    failures = 0
    for record in records:
        problems = check_against_input(record)
        notes = []
        if args.baseline:
            more, notes = check_against_baseline(record, baseline.get(record["id"]), args.identical,
                                                 set(args.expect_change))
            problems += more
        record["problems"], record["notes"] = problems, notes
        failures += bool(problems)
        state = "FAIL" if problems else "ok  "
        outcome = record["status"] if record["status"] == "cleaned" else "refused: " + record["message"][:70]
        print("%s %-4s %-44s %s" % (state, record["id"], record["label"][:44], outcome))
        for line in problems:
            print("       ! " + line)
        for line in notes:
            print("       - " + line[:300])

    label = "%s-%s" % (datetime.datetime.now().strftime("%Y%m%d-%H%M%S"), git_label(Path(__file__).parent))
    (corpus / "runs").mkdir(exist_ok=True)
    saved = corpus / "runs" / (label + ".json")
    saved.write_text(json.dumps({"label": label, "records": records}, ensure_ascii=False, indent=1))
    cleaned = sum(r["status"] == "cleaned" for r in records)
    print("\n%d samples: %d cleaned, %d refused, %d with problems. Saved %s"
          % (len(records), cleaned, len(records) - cleaned, failures, saved))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
