"""Clean one photo: rebuild it, check the result, and save a copy next to it."""
import errno
import hashlib
import os
import subprocess
import sys
from pathlib import Path

from . import exiftool, formats, names, pixels
from .errors import FormatError, InputError, VerificationError


def clean(argument, exiftool_path=None, naming="plain"):
    """Save a cleaned copy of a photo next to it and return the copy's path.

    The format's module rebuilds the file from what is needed to show it and
    checks the result on its own; Pillow must decode identical frames where it
    can; ExifTool, when `exiftool_path` is given, reads it independently.
    Nothing is saved unless every check passes. `naming` is described in
    names.candidates.
    """
    source = Path(os.path.abspath(os.path.expanduser(argument)))
    if not source.is_file():
        raise InputError("not_a_file", path=source)
    data = source.read_bytes()
    kind = formats.identify(data)
    if kind is None:
        raise InputError("unsupported_format")
    module = formats.MODULES[kind]
    rebuilt = module.rebuild(data)
    try:
        module.verify(data, rebuilt)
    except FormatError:
        # The format's reader could not parse the result back.
        raise VerificationError("verification_failed", detail="the result cannot be read back")
    if pixels.decodes(kind):
        # A format may drop some frames by design, as JPEG drops previews.
        kept = getattr(module, "kept_frames", None)
        pixels.compare(data, rebuilt, kind, kept(data) if kept else None)
    if exiftool_path:
        exiftool.second_opinion(exiftool_path, rebuilt, kind)
    if hashlib.sha256(data).digest() != file_digest(source):
        raise VerificationError("source_changed")
    suffixes = formats.SUFFIXES[kind]
    suffix = source.suffix if source.suffix.lower() in suffixes else suffixes[0]
    return publish(rebuilt, source, suffix, naming)


def file_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.digest()


def publish(data, source, suffix, naming="plain"):
    """Write data next to source under a new name; never overwrite anything."""
    for name in names.candidates(source.stem, suffix, naming):
        destination = source.with_name(name)
        try:
            output = destination.open("xb")
        except FileExistsError:
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
