"""Clean one photo: rebuild it, check the result, and save a copy next to it."""
import errno
import hashlib
import io
import os
import secrets
import subprocess
import sys
from pathlib import Path

from PIL import Image, features

from . import exiftool, formats
from .errors import FormatError, InputError, VerificationError


def clean(argument, exiftool_path=None, anonymous=False):
    """Save a cleaned copy of a photo next to it and return the copy's path.

    The format's module rebuilds the file from what is needed to show it and
    checks the result on its own; Pillow must decode identical frames where it
    can; ExifTool, when `exiftool_path` is given, reads it independently.
    Nothing is saved unless every check passes.
    """
    source = Path(os.path.abspath(os.path.expanduser(argument)))
    if not source.is_file():
        raise InputError("not_a_file", path=source)
    data = source.read_bytes()
    kind = formats.identify(data)
    if kind is None:
        raise InputError("unsupported_format")
    module = formats.REBUILT[kind]
    rebuilt = module.rebuild(data)
    module.verify(data, rebuilt)
    if pillow_decodes(kind):
        compare_pixels(data, rebuilt, kind)
    suffixes = formats.SUFFIXES[kind]
    if exiftool_path:
        exiftool.second_opinion(exiftool_path, source.parent, rebuilt, suffixes[0], kind)
    if hashlib.sha256(data).digest() != file_digest(source):
        raise VerificationError("source_changed")
    suffix = source.suffix if source.suffix.lower() in suffixes else suffixes[0]
    return publish(rebuilt, source, suffix, anonymous=anonymous)


def pillow_decodes(kind):
    return kind in formats.PILLOW_DECODES and (kind != "AVIF" or features.check("avif"))


def compare_pixels(original, rebuilt, kind):
    """Pillow must decode the same frames, timing and transparency from both."""
    try:
        before = pixels_digest(original)
    except (OSError, SyntaxError, ValueError):
        # Without a decodable original there is nothing to compare against.
        raise FormatError("damaged", format=kind)
    try:
        after = pixels_digest(rebuilt)
    except (OSError, SyntaxError, ValueError):
        raise VerificationError("verification_failed", detail="the result cannot be decoded")
    if before != after:
        raise VerificationError("pixels_changed")


def pixels_digest(data):
    """A digest of every displayed frame with its timing, transparency and repeat count."""
    digest = hashlib.sha256()
    with Image.open(io.BytesIO(data)) as picture:
        frames = getattr(picture, "n_frames", 1)
        digest.update(repr((picture.size, frames, picture.info.get("loop"),
                            picture.info.get("default_image", False))).encode())
        for index in range(frames):
            picture.seek(index)
            picture.load()
            digest.update(repr((picture.size, picture.info.get("duration", 0))).encode())
            digest.update(picture.convert("RGBA").tobytes())
            if picture.format == "TIFF":
                # Converting to RGBA would hide differences in 16-bit and CMYK samples.
                digest.update(picture.mode.encode())
                digest.update(picture.tobytes())
    return digest.digest()


def file_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.digest()


def publish(data, source, suffix, anonymous=False):
    """Write data next to source under a new name; never overwrite anything."""
    index = 0
    while True:
        tail = "_clean" if index == 0 else "_clean_" + str(index)
        name = ("photo_" + secrets.token_hex(16) + suffix.lower() if anonymous
                else source.stem + tail + suffix)
        destination = source.with_name(name)
        try:
            output = destination.open("xb")
        except FileExistsError:
            index += 1
            continue
        try:
            with output:
                output.write(data)
            clear_attributes(destination)
            return destination
        except BaseException:
            destination.unlink(missing_ok=True)
            raise


def clear_attributes(path):
    """The copy is written from verified bytes, so it has none of the original's
    extended attributes, resource forks or alternate streams; clear any the
    system added to the new file."""
    if sys.platform == "darwin":
        result = subprocess.run(["/usr/bin/xattr", "-c", str(path)], capture_output=True)
        if result.returncode:
            raise OSError("could not clear extended attributes: "
                          + result.stderr.decode("utf-8", "replace").strip())
    elif hasattr(os, "listxattr"):
        try:
            for attribute in os.listxattr(path):
                if attribute.startswith("user."):
                    os.removexattr(path, attribute)
        except OSError as error:
            if error.errno not in (errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS):
                raise
