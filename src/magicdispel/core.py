"""Clean one photo or video: rebuild it, check the result, and save a copy next to it."""
import errno
import hashlib
import mmap
import os
import secrets
import shutil
import stat
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

from . import exiftool, formats, names, pixels
from .errors import FormatError, InputError, VerificationError

HEAD = 4096  # enough of a file to tell a video from a photo


def clean(argument, exiftool_path=None, naming="plain"):
    """Save a cleaned copy of a photo or video next to it and return the copy's path.

    The format's module rebuilds the file from what is needed to show it and
    checks the result on its own; Pillow must decode identical frames where it
    can; ExifTool, when `exiftool_path` is given, reads it independently.
    Nothing is saved unless every check passes. `naming` is described in
    names.candidates. Videos are cleaned in a copy of themselves (clean_copy).
    """
    source = Path(os.path.abspath(os.path.expanduser(argument)))
    if not source.is_file():
        raise InputError("not_a_file", path=source)
    with source.open("rb") as stream:
        video = formats.video_format(stream.read(HEAD))
    if video:
        return clean_copy(source, video, exiftool_path, naming)
    data = source.read_bytes()
    kind = formats.identify(data)
    if kind is None:
        raise InputError("unsupported_format")
    if kind in formats.VIDEOS:  # told apart only past the first bytes, by a long file type box
        return clean_copy(source, kind, exiftool_path, naming)
    module = formats.MODULES[kind]
    rebuilt = module.rebuild(data)
    verify(module, data, rebuilt)
    if pixels.decodes(kind):
        # A file of several pictures is compared picture by picture.
        pictures = getattr(module, "pictures", lambda file: [file])
        before, after = pictures(data), pictures(rebuilt)
        if len(before) != len(after):
            raise VerificationError("pixels_changed")
        for original_picture, rebuilt_picture in zip(before, after):
            pixels.compare(original_picture, rebuilt_picture, kind)
    if exiftool_path:
        exiftool.second_opinion(exiftool_path, rebuilt, kind)
    if hashlib.sha256(data).digest() != file_digest(source):
        raise VerificationError("source_changed")
    return publish(rebuilt, source, output_suffix(source, kind), naming)


def clean_copy(source, kind, exiftool_path=None, naming="plain"):
    """Clean a video in a copy of itself next to it. Neither file is read into
    memory: media data never moves, so only a few boxes and the unused media
    bytes of the copy change. The copy is checked like any other result before
    it gets its name, and removed if it fails."""
    module = formats.MODULES[kind]
    digest = file_digest(source)
    suffix = output_suffix(source, kind)
    partial = reserve(source, suffix)
    try:
        shutil.copyfile(source, partial)
        if partial.stat().st_size != source.stat().st_size:
            raise VerificationError("source_changed")
        with mapped(source) as original, mapped(partial, writable=True) as copy:
            if len(copy) != len(original):
                raise VerificationError("source_changed")
            length = module.clean(original, copy)
        os.truncate(partial, length)
        with mapped(source) as original, mapped(partial) as rebuilt:
            verify(module, original, rebuilt)
        if exiftool_path:
            exiftool.second_opinion(exiftool_path, None, kind, path=partial)
        if file_digest(source) != digest:
            raise VerificationError("source_changed")
        return rename(partial, source, suffix, naming, "video")
    finally:
        partial.unlink(missing_ok=True)


def verify(module, original, rebuilt):
    try:
        module.verify(original, rebuilt)
    except FormatError:
        # The format's reader could not parse the result back.
        raise VerificationError("verification_failed", detail="the result cannot be read back")


@contextmanager
def mapped(path, writable=False):
    """A file's bytes, mapped into memory rather than read."""
    with path.open("r+b" if writable else "rb") as stream:
        try:
            view = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_WRITE if writable else mmap.ACCESS_READ)
        except ValueError:  # an empty file: a video emptied while it was being cleaned
            raise VerificationError("source_changed")
        try:
            yield view
            if writable:
                view.flush()
        finally:
            view.close()


def output_suffix(source, kind):
    """The original's extension if it suits the format, else the format's own."""
    suffixes = formats.SUFFIXES[kind]
    return source.suffix if source.suffix.lower() in suffixes else suffixes[0]


def reserve(source, suffix):
    """A new, empty file next to the original, to clean a copy in. Should
    MagicDispel be stopped where it cannot remove it (killed, or the
    original's drive unplugged), its name says what it is."""
    while True:
        partial = source.with_name("magicdispel-%s.unfinished%s" % (secrets.token_hex(4), suffix))
        try:
            create(partial).close()
            return partial
        except FileExistsError:
            continue


def create(path):
    """A new file that only its owner can read, open for writing; FileExistsError
    if there is one. A copy gets the original's permissions once it is clean."""
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600), "wb")


def permissions(path):
    return stat.S_IMODE(path.stat().st_mode)


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
            output = create(destination)
        except FileExistsError:
            continue
        try:
            with output:
                output.write(data)
            os.chmod(destination, permissions(source))
            clear_attributes(destination)
            return destination
        except BaseException:
            destination.unlink(missing_ok=True)
            raise


def rename(path, source, suffix, naming="plain", noun="photo"):
    """Give a finished copy a new name next to source; never overwrite anything."""
    for name in names.candidates(source.stem, suffix, naming, noun):
        destination = source.with_name(name)
        try:
            create(destination).close()  # claims the name, which the copy then takes
        except FileExistsError:
            continue
        try:
            os.replace(path, destination)
            os.chmod(destination, permissions(source))
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
