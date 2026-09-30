"""Damage small files of every format and clean them, to see that a damaged file is refused and never
crashes, stalls or fools the program.

    python scripts/fuzz.py 20000 --workers 8 --keep ~/fuzz-cases

Each worker makes files (Pillow's encoders, and the builders of the unit tests for HEIC and video),
damages one at random and cleans it in this process, without ExifTool. Two kinds of damage: bytes are
changed, cut, doubled, swapped, set to values that break sizes, or given long runs of one byte; and, so that
the files get past the first checks, chunks, segments, blocks, boxes and TIFF entries are doubled, dropped,
swapped, retyped and given other contents, with their sizes and checksums made right again.

Reported, with the file saved in the folder of --keep: an exception that is not a refusal (a bug), a file
that takes more than 8 seconds, and a clean copy that does not clean to itself. Exit code 1 if any is.
"""
import argparse
import hashlib
import io
import os
import random
import struct
import sys
import tempfile
import time
import traceback
import warnings
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
warnings.simplefilter("ignore")

from PIL import Image, ImageCms, PngImagePlugin

import test_heif
import test_mp4
from magicdispel import core
from magicdispel.errors import UserError
from magicdispel.formats import bmff, gif, jpeg, png, webp

SLOW = 8  # seconds
SUFFIXES = {"jpeg": ".jpg", "png": ".png", "gif": ".gif", "webp": ".webp", "tiff": ".tif", "bmp": ".bmp",
            "heic": ".heic", "mov": ".mov", "mp4": ".mp4"}
PNG_KINDS = [b"PLTE", b"tRNS", b"gAMA", b"cHRM", b"sRGB", b"iCCP", b"cICP", b"sBIT", b"mDCV", b"cLLI", b"pHYs",
             b"bKGD", b"acTL", b"fcTL", b"fdAT", b"IDAT", b"tEXt", b"eXIf", b"IHDR", b"IEND", b"zzzz"]
WEBP_KINDS = [b"VP8 ", b"VP8L", b"VP8X", b"ALPH", b"ANIM", b"ANMF", b"ICCP", b"EXIF", b"XMP ", b"zzzz"]
JPEG_MARKERS = [0xDB, 0xC4, 0xDD, 0xDC, 0xCC, 0xE0, 0xE1, 0xE2, 0xEA, 0xEE, 0xFE, 0xC0, 0xC2, 0xDA, 0x01, 0xD0]
GIF_BLOCKS = [b"\x21\xfe\x02hi\x00", b"\x21\xff\x0bNETSCAPE2.0\x03\x01\x05\x00\x00",
              b"\x21\xf9\x04\x05\x0a\x00\x01\x00", b"\x21\x01\x0c" + bytes(12) + b"\x00",
              b"\x21\xff\x0bICCRGBG1012\x02ab\x00"]
BOX_KINDS = [b"free", b"uuid", b"skip", b"udta", b"meta", b"stsd", b"sdtp", b"tref", b"pasp", b"trak", b"mvhd", b"zzzz"]
CONTAINERS = {b"meta": 4, b"moov": 0, b"trak": 0, b"mdia": 0, b"minf": 0, b"stbl": 0, b"iprp": 0, b"ipco": 0,
              b"dinf": 0, b"edts": 0, b"iinf": 6, b"iref": 4}  # and the bytes of a full box's header before its boxes
TIFF_TAGS = [254, 255, 256, 257, 258, 259, 262, 263, 266, 273, 274, 277, 278, 279, 280, 281, 282, 283, 284, 290, 291,
             292, 293, 296, 297, 301, 317, 318, 319, 320, 321, 322, 323, 324, 325, 330, 334, 336, 338, 339, 340, 341,
             342, 347, 529, 530, 531, 532, 34675, 700, 33432, 50706]
NUMBERS = [0, 1, 2, 3, 4, 8, 16, 255, 256, 65535, 65536, 1 << 24, 0x7FFFFFFF, 0xFFFFFFFF]


def seeds():
    """{name: bytes} of small valid files."""
    def encode(image, format, **options):
        stream = io.BytesIO()
        image.save(stream, format, **options)
        return stream.getvalue()

    image = Image.new("RGB", (48, 32))
    image.putdata([((x * 5) % 256, (y * 8) % 256, (x * y) % 256) for y in range(32) for x in range(48)])
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    exif = Image.Exif()
    exif[0x0112], exif[0x010F] = 6, "Maker"
    text = PngImagePlugin.PngInfo()
    text.add_text("k", "v")
    frames = [image.convert("RGBA").rotate(90 * n) for n in range(3)]
    return {
        "jpeg": encode(image, "JPEG", quality=85, exif=exif.tobytes(), icc_profile=profile, dpi=(72, 72)),
        "jpeg_progressive": encode(image, "JPEG", progressive=True, optimize=True),
        "jpeg_restarts": encode(image, "JPEG", restart_marker_rows=1),
        "jpeg_cmyk": encode(image.convert("CMYK"), "JPEG"),
        "png": encode(image.convert("RGBA"), "PNG", pnginfo=text, icc_profile=profile, dpi=(96, 96)),
        "png_palette": encode(image.convert("P"), "PNG", transparency=0),
        "png_animated": encode(frames[0], "PNG", save_all=True, append_images=frames[1:], duration=50, loop=2),
        "gif": encode(image.convert("P"), "GIF", save_all=True, append_images=[image.convert("P").rotate(90)],
                      duration=[30, 60], loop=1, comment=b"c"),
        "webp": encode(image, "WEBP", quality=70, icc_profile=profile, exif=exif.tobytes()),
        "webp_animated": encode(frames[0], "WEBP", save_all=True, append_images=frames[1:], duration=50, lossless=True),
        "tiff": encode(image, "TIFF", dpi=(72, 72)),
        "tiff_palette": encode(image.convert("P"), "TIFF"),
        "tiff_jpeg": encode(image, "TIFF", compression="jpeg"),
        "tiff_pages": encode(image, "TIFF", save_all=True, append_images=[image.rotate(90)]),
        "bmp": encode(image, "BMP"),
        "heic": test_heif.heif_file([test_heif.PRIMARY, (2, b"Exif", b"EXIFDATA"), (3, b"mime", test_heif.HDR_XMP)],
                                    refs=[(b"cdsc", 2, [1]), (b"cdsc", 3, [1])]),
        "heic_depth": test_heif.heif_file([test_heif.PRIMARY, test_heif.DEPTH_IMAGE], [(b"auxl", 2, [1])],
                                          {2: test_heif.DEPTH}),
        "mov": test_mp4.movie_file(),
        "mp4": test_mp4.movie_file(quicktime=False),
        "mp4_plain": test_mp4.plain_video(),
    }


# ------------------------------------------------------------------------------------------ damaging bytes

def damage(rng, data):
    """`data` with one to six changes of bytes."""
    data = bytearray(data)
    for _ in range(rng.choice([1, 1, 1, 2, 3, 6])):
        size = len(data)
        if size < 16:
            break
        kind = rng.randrange(10)
        if kind == 0:
            for _ in range(rng.randint(1, 4)):
                data[rng.randrange(size)] = rng.randrange(256)
        elif kind == 1:
            del data[rng.randrange(8, size):]
        elif kind == 2:
            start = rng.randrange(size)
            del data[start:start + rng.choice([1, 2, 4, 8, 16, 64, 256])]
        elif kind == 3:
            start = rng.randrange(size)
            data[start:start] = data[start:start + rng.choice([2, 4, 8, 16, 64, 256, 1024])]
        elif kind == 4:
            start = rng.randrange(size)
            data[start:start] = random_bytes(rng)
        elif kind == 5:
            start = rng.randrange(0, size - 4) & ~1
            data[start:start + 4] = struct.pack(rng.choice([">I", "<I"]), rng.choice(NUMBERS + [size, size + 1]))
        elif kind == 6:
            start = rng.randrange(0, size - 2)
            data[start:start + 2] = struct.pack(rng.choice([">H", "<H"]), rng.choice([0, 1, 2, 255, 0xFFFF, 0x8000]))
        elif kind == 7:
            first, second = sorted((rng.randrange(size), rng.randrange(size)))
            length = rng.choice([4, 8, 16, 64])
            if second + length <= size and first + length <= second:
                one, other = bytes(data[first:first + length]), bytes(data[second:second + length])
                data[first:first + length], data[second:second + length] = other, one
        elif kind == 8:
            start = rng.randrange(size)
            data[start:start] = long_run(rng)
        else:
            start = rng.randrange(size)
            data[start:start + rng.choice([1, 2, 4, 8, 32])] = bytes(rng.choice([1, 2, 4, 8, 32]))
    return bytes(data)


def random_bytes(rng, size=None):
    return bytes(rng.randrange(256) for _ in range(rng.choice([0, 1, 2, 4, 8, 16, 40]) if size is None else size))


def long_run(rng):
    """One byte many times, then perhaps a zero or another byte: code that looks over a run again from each of
    its bytes, to see what follows it, takes minutes."""
    run = bytes([rng.choice([0x00, 0xFF, rng.randrange(256)])]) * rng.choice([256, 4096, 1 << 16])
    return run + rng.choice([b"", b"\x00", random_bytes(rng, 1)])


def change(rng, payload):
    """`payload` with some of its bytes changed, cut, added or replaced."""
    payload = bytearray(payload)
    kind = rng.randrange(7)
    if kind == 0 and payload:
        for _ in range(rng.randint(1, 3)):
            payload[rng.randrange(len(payload))] = rng.randrange(256)
    elif kind == 1 and payload:
        del payload[rng.randrange(len(payload)):]
    elif kind == 2:
        payload += random_bytes(rng)
    elif kind == 3 and payload:
        start = rng.randrange(len(payload))
        payload[start:start] = payload[start:start + rng.choice([1, 4, 16])]
    elif kind == 4:
        payload = bytearray(random_bytes(rng))
    elif kind == 5:
        start = rng.randrange(len(payload) + 1)
        payload[start:start] = long_run(rng)
    elif payload:
        payload[rng.randrange(len(payload))] = rng.choice([0, 0xFF])
    return bytes(payload)


# ----------------------------------------------------------------------- damaging what a file is made of

def rearrange(rng, items, make):
    """`items` with one to three of them doubled, dropped, swapped, changed, retyped, or a new one added;
    `make(item, how)` gives the changed, retyped or new item."""
    items = list(items)
    for _ in range(rng.choice([1, 1, 2, 3])):
        if not items:
            break
        index, action = rng.randrange(len(items)), rng.randrange(6)
        if action == 0:
            items.insert(index, items[index])
        elif action == 1:
            del items[index]
        elif action == 2 and len(items) > 1:
            other = rng.randrange(len(items))
            items[index], items[other] = items[other], items[index]
        elif action == 3:
            items[index] = make(items[index], "changed")
        elif action == 4:
            items.insert(rng.randrange(len(items) + 1), make(None, "new"))
        else:
            items[index] = make(items[index], "retyped")
    return items


def rearrange_png(rng, data):
    def make(item, how):
        if how == "new":
            return rng.choice(PNG_KINDS), random_bytes(rng)
        return (rng.choice(PNG_KINDS), item[1]) if how == "retyped" else (item[0], change(rng, item[1]))

    parts = rearrange(rng, [(kind, payload) for kind, payload, _ in png.chunks(data)], make)
    return png.SIGNATURE + b"".join(png.serialize(kind, payload) for kind, payload in parts)


def rearrange_webp(rng, data):
    def make(item, how):
        if how == "new":
            return rng.choice(WEBP_KINDS), random_bytes(rng)
        return (rng.choice(WEBP_KINDS), item[1]) if how == "retyped" else (item[0], change(rng, item[1]))

    return webp.serialize(rearrange(rng, webp.chunks(data), make))


def rearrange_jpeg(rng, data):
    """Segments as (marker, its bytes, its payload)."""
    def segment(marker, payload):
        if marker in (0x01, 0xD0):
            return marker, b"\xff" + bytes([marker]), b""
        return marker, b"\xff" + bytes([marker]) + struct.pack(">H", len(payload) + 2) + payload, payload

    def make(item, how):
        if how == "new":
            return segment(rng.choice(JPEG_MARKERS), random_bytes(rng))
        if item[0] in (0xD8, 0xD9):
            return item
        if item[0] == 0xDA:  # a scan keeps its data, and its header may change
            head = 4 + len(item[2])
            payload = change(rng, item[2])[:len(item[2])].ljust(len(item[2]), b"\0")
            return item[0], item[1][:4] + payload + item[1][head:], item[2]
        if how == "retyped":
            return segment(rng.choice(JPEG_MARKERS), item[2])
        return segment(item[0], change(rng, item[2])[:65533])

    parts = [(marker, data[start:end], payload) for marker, start, end, payload in jpeg.segments(data)]
    return b"".join(raw for _, raw, _ in rearrange(rng, parts, make))


def rearrange_gif(rng, data):
    def make(item, how):
        if how == "new":
            return rng.choice(GIF_BLOCKS)
        if item[:1] == b"G" or item == b";":
            return item
        changed = bytearray(item)
        if len(changed) > 2:
            changed[rng.randrange(len(changed))] = rng.randrange(256)
        return bytes(changed)

    return b"".join(rearrange(rng, [block for _, block in gif.blocks(data)], make))


def rearrange_boxes(rng, data):
    """Boxes, and those in the containers among them, as (type, the bytes before their boxes or None, contents)."""
    def parse(start, end, depth):
        found = []
        for box in bmff.boxes(data, start, end):
            skip = CONTAINERS.get(box.kind)
            if skip is not None and depth < 4:
                try:
                    found.append((box.kind, data[box.content:box.content + skip],
                                  parse(box.content + skip, box.end, depth + 1)))
                    continue
                except (bmff.StructureError, struct.error):
                    pass
            found.append((box.kind, None, data[box.content:box.end]))
        return found

    def dump(nodes):
        out = b""
        for kind, head, body in nodes:
            payload = head + dump(body) if head is not None else body
            out += struct.pack(">I4s", len(payload) + 8, kind) + payload
        return out

    def every(nodes):
        for node in nodes:
            yield nodes, node
            if node[1] is not None:
                yield from every(node[2])

    tree = parse(0, len(data), 0)
    for _ in range(rng.choice([1, 1, 2])):
        nodes = rng.choice([container for container, _ in every(tree)]) if rng.random() < 0.5 else tree
        if not nodes:
            continue
        index, action = rng.randrange(len(nodes)), rng.randrange(6)
        kind, head, body = nodes[index]
        if action == 0:
            nodes.insert(index, nodes[index])
        elif action == 1:
            del nodes[index]
        elif action == 2 and len(nodes) > 1:
            other = rng.randrange(len(nodes))
            nodes[index], nodes[other] = nodes[other], nodes[index]
        elif action == 3 and head is None:
            nodes[index] = (kind, head, change(rng, body))
        elif action == 4:
            nodes.insert(rng.randrange(len(nodes) + 1), (rng.choice(BOX_KINDS), None, random_bytes(rng)))
        else:
            nodes[index] = (rng.choice(BOX_KINDS), None, body if head is None else b"")
    return dump(tree)


def rearrange_tiff(rng, data):
    """Entries of its directories changed in place, doubled, dropped or added, or a page that shares another's data."""
    data, order = bytearray(data), "<" if data[:2] == b"II" else ">"

    def number(fmt, offset):
        return struct.unpack_from(order + fmt, data, offset)[0]

    directories, offset = [], number("I", 4)
    while offset and len(directories) < 6 and offset + 2 <= len(data):
        count = number("H", offset)
        if offset + 2 + 12 * count + 4 > len(data):
            break
        directories.append((offset, count))
        offset = number("I", offset + 2 + 12 * count)
    if not directories:
        raise ValueError("no directory")

    def append(entries, index, following):
        """A new directory of `entries` in place of number `index`."""
        entries = sorted(entries, key=lambda entry: struct.unpack_from(order + "H", entry)[0])
        start = len(data)
        data.extend(struct.pack(order + "H", len(entries)) + b"".join(entries) + struct.pack(order + "I", following))
        link = 4 if index == 0 else directories[index - 1][0] + 2 + 12 * directories[index - 1][1]
        struct.pack_into(order + "I", data, link, start)
        return start, len(entries)

    for _ in range(rng.choice([1, 1, 2])):
        index = rng.randrange(len(directories))
        start, count = directories[index]
        entries = [bytes(data[start + 2 + 12 * n:start + 14 + 12 * n]) for n in range(count)]
        following, action = number("I", start + 2 + 12 * count), rng.randrange(6)
        if action == 0 and entries:
            n, field = rng.randrange(count), rng.randrange(4)
            entry = bytearray(entries[n])
            if field == 0:
                struct.pack_into(order + "H", entry, 0, rng.choice(TIFF_TAGS))
            elif field == 1:
                struct.pack_into(order + "H", entry, 2, rng.randrange(15))
            elif field == 2:
                struct.pack_into(order + "I", entry, 4, rng.choice(NUMBERS))
            else:
                struct.pack_into(order + "I", entry, 8, rng.choice(NUMBERS + [len(data) - 8, len(data)]))
            data[start + 2 + 12 * n:start + 14 + 12 * n] = entry
        elif action == 1 and entries:
            entries.append(rng.choice(entries))
            directories[index] = append(entries, index, following)
        elif action == 2 and count > 1:
            del entries[rng.randrange(count)]
            directories[index] = append(entries, index, following)
        elif action == 3:  # a page of the same entries, so of the same data, after this one
            new = len(data)
            data.extend(struct.pack(order + "H", count) + b"".join(entries) + struct.pack(order + "I", following))
            struct.pack_into(order + "I", data, start + 2 + 12 * count, new)
            directories.insert(index + 1, (new, count))
        elif action == 4:
            entries.append(struct.pack(order + "HHII", rng.choice(TIFF_TAGS), rng.choice([1, 3, 3, 4, 5, 7, 12]),
                                       rng.choice([1, 1, 2, 3, 6, 256, 768]),
                                       rng.choice([len(data) - 16, 0, len(data) // 2, 8])))
            directories[index] = append(entries, index, following)
        elif entries:
            n = rng.randrange(count)
            data[start + 10 + 12 * n:start + 14 + 12 * n] = struct.pack(order + "I", rng.choice(NUMBERS))
    return bytes(data)


REARRANGE = {"png": rearrange_png, "webp": rearrange_webp, "jpeg": rearrange_jpeg, "gif": rearrange_gif,
             "heic": rearrange_boxes, "mov": rearrange_boxes, "mp4": rearrange_boxes, "tiff": rearrange_tiff}


# --------------------------------------------------------------------------------------------- the runs

def kind_of(name):
    return next(kind for kind in SUFFIXES if name.startswith(kind))


def worker(job):
    index, count, seed, keep = job
    rng = random.Random("%s-%d" % (seed, index))
    files = seeds()
    names = sorted(files)
    reports, tally = [], {"cleaned": 0, "refused": 0, "not made": 0}
    with tempfile.TemporaryDirectory(prefix="fuzz-") as folder:
        for number in range(count):
            name = rng.choice(names)
            kind = kind_of(name)
            rearranger = REARRANGE.get(kind)
            try:
                if rearranger and rng.random() < 0.5:
                    data = rearranger(rng, files[name])
                    if rng.random() < 0.35:
                        data = rearranger(rng, data)  # twice, so that what they do meets
                else:
                    data = damage(rng, files[name])
            except (ValueError, KeyError, IndexError, struct.error, UserError):
                tally["not made"] += 1
                continue
            path = Path(folder, "input" + SUFFIXES[kind])
            path.write_bytes(data)
            started = time.monotonic()
            outcome, problem = check(path, folder)
            tally[outcome] += 1
            if time.monotonic() - started > SLOW:
                problem = "slow, %.0f seconds%s" % (time.monotonic() - started, "; " + problem if problem else "")
            if problem:
                digest = hashlib.sha1(data).hexdigest()[:10]
                reports.append((problem, name, digest))
                if keep:
                    label = problem.split()[0].strip(",;:")
                    Path(keep, "%s-%s-%s%s" % (label, name, digest, SUFFIXES[kind])).write_bytes(data)
            for leftover in Path(folder).iterdir():
                if leftover != path:
                    leftover.unlink()
    return tally, reports


def check(path, folder):
    """Clean `path`, and its clean copy again: ("cleaned" or "refused", what is wrong with it, or None)."""
    try:
        copy = core.clean(str(path))
    except UserError:
        return "refused", None
    except BaseException as error:  # a bug, whatever it is
        frames = traceback.extract_tb(error.__traceback__)[-2:]
        where = ", ".join("%s:%d" % (Path(frame.filename).name, frame.lineno) for frame in frames)
        return "cleaned", "exception %s: %s at %s" % (type(error).__name__, str(error)[:100], where)
    again = Path(folder, "again" + path.suffix)
    again.write_bytes(copy.read_bytes())
    try:
        second = core.clean(str(again))
    except UserError as error:
        return "cleaned", "refused-again: " + str(error)[:100]
    except BaseException as error:
        return "cleaned", "exception-again %s: %s" % (type(error).__name__, str(error)[:100])
    return "cleaned", None if second.read_bytes() == copy.read_bytes() else "not-idempotent"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("count", type=int, help="files to make and clean, in each worker")
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    parser.add_argument("--seed", default="0", help="the same seed makes the same files")
    parser.add_argument("--keep", help="a folder to save the files that are reported in")
    args = parser.parse_args()
    if args.keep:
        Path(args.keep).expanduser().mkdir(parents=True, exist_ok=True)
    keep = str(Path(args.keep).expanduser()) if args.keep else None
    with Pool(args.workers) as pool:
        results = pool.map(worker, [(index, args.count, args.seed, keep) for index in range(args.workers)])
    total = {}
    for tally, reports in results:
        for key, value in tally.items():
            total[key] = total.get(key, 0) + value
        for report, name, digest in reports:
            print("%s: %s (%s)" % (report, name, digest))
    print("cleaned %(cleaned)d, refused %(refused)d, not made %(not made)d" % total)
    sys.exit(1 if any(reports for _, reports in results) else 0)


if __name__ == "__main__":
    main()
