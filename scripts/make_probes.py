#!/usr/bin/env python3
"""Add synthetic leak probes to a regression corpus.

Each probe hides MARKER in a structure that metadata tools tend to overlook:
private PNG chunks, unknown WebP chunks, GIF application extensions, top-level
HEIF/AVIF boxes and so on. A cleaned output must never contain the marker;
refusing the file is also acceptable. HEIC probes need macOS (sips).

    python scripts/make_probes.py CORPUS
"""
import io
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

from PIL import Image, TiffImagePlugin

from regression import load_manifest

MARKER = b"SECRET-40.7128N-74.0060W-JaneDoe"


def gradient():
    image = Image.new("RGB", (32, 24))
    image.putdata([(x * 8, y * 10, (x * y) % 256) for y in range(24) for x in range(32)])
    return image


def encoded(format, **options):
    buffer = io.BytesIO()
    image = gradient().convert("P") if format == "GIF" else gradient()
    image.save(buffer, format, **options)
    return buffer.getvalue()


def png_with_chunk(kind, body):
    data = encoded("PNG")
    chunk = struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    end = data.rindex(b"IEND") - 4
    return data[:end] + chunk + data[end:]


def jpeg_with_segment(marker, payload):
    data = encoded("JPEG", quality=90)
    return data[:2] + bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload + data[2:]


def jumbf_box():
    """A small, deliberately incomplete C2PA-style JUMBF superbox."""
    description = bytes.fromhex("6332706100110010800000aa00389b71") + b"\x03c2pa\0"
    boxes = (struct.pack(">I", 8 + len(description)) + b"jumd" + description
             + struct.pack(">I", 8 + len(MARKER)) + b"json" + MARKER)
    return struct.pack(">I", 8 + len(boxes)) + b"jumb" + boxes


def webp_with_chunk():
    data = encoded("WEBP", quality=90, exif=b"Exif\0\0MM\0*\0\0\0\x08\0\0\0\0\0\0")
    data += b"SECR" + struct.pack("<I", len(MARKER)) + MARKER
    return data[:4] + struct.pack("<I", len(data) - 8) + data[8:]


def gif_with_application_extension():
    data = encoded("GIF")
    start = 13 + (3 << ((data[10] & 7) + 1) if data[10] & 0x80 else 0)
    extension = b"\x21\xff\x0bSECRETAP1.0" + bytes([len(MARKER)]) + MARKER + b"\0"
    return data[:start] + extension + data[start:]


def tiff_with_private_tag():
    tags = TiffImagePlugin.ImageFileDirectory_v2()
    tags[65000] = MARKER.decode()
    tags.tagtype[65000] = 2
    buffer = io.BytesIO()
    gradient().save(buffer, "TIFF", tiffinfo=tags)
    return buffer.getvalue()


def heic():
    if not shutil.which("sips"):
        return None
    with tempfile.TemporaryDirectory() as folder:
        source, target = Path(folder, "seed.png"), Path(folder, "seed.heic")
        gradient().save(source)
        subprocess.run(["sips", "-s", "format", "heic", str(source), "--out", str(target)],
                       check=True, capture_output=True)
        return target.read_bytes()


def with_box(data, kind, payload):
    return None if data is None else data + struct.pack(">I", 8 + len(payload)) + kind + payload


UUID = bytes(range(16))
PROBES = [
    ("P01", "PNG private chunk", "png-private.png", lambda: png_with_chunk(b"prVt", MARKER)),
    ("P02", "PNG caBX with malformed C2PA", "png-c2pa.png", lambda: png_with_chunk(b"caBX", jumbf_box())),
    ("P03", "JPEG unknown APP9 segment", "jpeg-app9.jpg", lambda: jpeg_with_segment(0xE9, b"PRIV\0" + MARKER)),
    ("P04", "JPEG APP11 with malformed C2PA", "jpeg-c2pa.jpg",
     lambda: jpeg_with_segment(0xEB, b"JP\0\x01\0\0\0\x01" + jumbf_box())),
    ("P05", "JPEG data after end of image", "jpeg-trailer.jpg", lambda: encoded("JPEG", quality=90) + MARKER),
    ("P06", "WebP unknown chunk", "webp-chunk.webp", webp_with_chunk),
    ("P07", "GIF application extension", "gif-extension.gif", gif_with_application_extension),
    ("P08", "TIFF private tag", "tiff-private.tif", tiff_with_private_tag),
    ("P09", "HEIC top-level uuid box", "heic-uuid.heic", lambda: with_box(heic(), b"uuid", UUID + MARKER)),
    ("P10", "HEIC free box with data", "heic-free.heic", lambda: with_box(heic(), b"free", MARKER)),
    ("P11", "AVIF top-level uuid box", "avif-uuid.avif", lambda: with_box(encoded("AVIF"), b"uuid", UUID + MARKER)),
]


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    corpus = Path(sys.argv[1])
    manifest_path = corpus / "manifest.json"
    # List the folder's own photos first, so the probes are added alongside them.
    manifest = [entry for entry in load_manifest(corpus) if not entry["id"].startswith("P")]
    for ident, label, name, build in PROBES:
        data = build()
        if data is None:
            print("skipped (needs macOS):", ident, label)
            continue
        stored = ident + Path(name).suffix
        (corpus / stored).write_bytes(data)
        manifest.append({"id": ident, "label": label, "file": stored, "name": name})
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print("probes written:", sum(entry["id"].startswith("P") for entry in manifest))


if __name__ == "__main__":
    main()
