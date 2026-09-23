"""Every format end to end: synthetic images saved by Pillow with private
metadata, cleaned, and checked independently of the code under test. With
ExifTool installed, it adds metadata as other programs write it and double-
checks each result. Never use personal photographs in this suite."""
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageCms, PngImagePlugin

from magicdispel import core, exiftool

MARKER = "PRIVATE_MARKER"
DATE = "2020:01:02 03:04:05"
XMP = ('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
       '<rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:creator><rdf:Seq><rdf:li>%s</rdf:li>'
       '</rdf:Seq></dc:creator></rdf:Description></rdf:RDF></x:xmpmeta>' % MARKER).encode()
PROFILE = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
EXIFTOOL = exiftool.find()
SECOND_CHECK = EXIFTOOL if EXIFTOOL and exiftool.usable(exiftool.version(EXIFTOOL)) else None
ARTIST, DATE_TIME, EXIF_IFD, GPS_IFD, DATE_TIME_ORIGINAL = 0x013B, 0x0132, 0x8769, 0x8825, 0x9003


def private_exif():
    tags = Image.Exif()
    tags[ARTIST], tags[DATE_TIME] = MARKER, DATE
    tags.get_ifd(EXIF_IFD)[DATE_TIME_ORIGINAL] = DATE
    gps = tags.get_ifd(GPS_IFD)
    gps[1], gps[2], gps[3], gps[4] = "N", (31.0, 13.0, 48.0), "E", (121.0, 28.0, 12.0)
    return tags


def private_text():
    info = PngImagePlugin.PngInfo()
    info.add_text("Comment", MARKER)
    info.add_itxt("XML:com.adobe.xmp", XMP.decode())
    return info


def samples():
    """(file name, image, Pillow format, save options) for each variant."""
    rgb = Image.new("RGB", (65, 49))
    rgb.putdata([((x * 17) % 256, (y * 11) % 256, ((x + y) * 7) % 256) for y in range(49) for x in range(65)])
    rgba = rgb.convert("RGBA")
    rgba.putalpha(Image.linear_gradient("L").resize(rgb.size))
    palette = rgba.quantize(colors=32)
    gray16 = Image.frombytes("I;16", rgb.size, bytes((i * 7) % 256 for i in range(65 * 49 * 2)))

    def flipped(image):
        return image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)

    jpeg = {"exif": private_exif(), "comment": MARKER, "xmp": XMP}
    png = {"exif": private_exif(), "pnginfo": private_text()}
    webp = {"exif": private_exif(), "xmp": XMP}
    tiff = {"tiffinfo": {270: MARKER, 306: DATE, 315: MARKER, 700: XMP}}
    gif = {"comment": MARKER}
    animation = {"save_all": True, "duration": [100, 250], "loop": 3}
    return [
        ("rgb.jpg", rgb, "JPEG", dict(jpeg, icc_profile=PROFILE)),
        ("progressive.JPG", rgb, "JPEG", dict(jpeg, progressive=True)),
        ("gray.jpeg", rgb.convert("L"), "JPEG", jpeg),
        ("cmyk.jpg", rgb.convert("CMYK"), "JPEG", jpeg),
        # No metadata from Pillow, which puts XMP after the MPF index: ExifTool then
        # leaves the other images' offsets wrong. ExifTool's own metadata goes before
        # the index; it updates the offsets and leaves the first image's size stale.
        ("multi-picture.jpg", rgb, "MPO", {"save_all": True, "append_images": [flipped(rgb)]}),
        ("rgb.png", rgb, "PNG", png),
        ("alpha.png", rgba, "PNG", png),
        ("palette.png", palette, "PNG", png),
        ("16bit.png", gray16, "PNG", png),
        ("animated.png", rgba, "PNG", dict(png, append_images=[flipped(rgba)], **animation)),
        ("poster.apng", rgba, "PNG", dict(png, append_images=[flipped(rgba), rgba], default_image=True,
                                          **dict(animation, duration=[100, 250, 50], loop=2))),
        ("rgb.webp", rgb, "WEBP", webp),
        ("lossless.webp", rgb, "WEBP", dict(webp, lossless=True, icc_profile=PROFILE)),
        ("alpha.webp", rgba, "WEBP", dict(webp, lossless=True)),
        ("animated.webp", rgba, "WEBP", dict(webp, append_images=[flipped(rgba)], **animation)),
        ("rgb.avif", rgb, "AVIF", webp),
        ("animated.avif", rgb, "AVIF", dict(webp, append_images=[flipped(rgb)], **animation)),
        ("photo.gif", rgb, "GIF", gif),
        ("alpha.gif", palette, "GIF", gif),
        ("animated.gif", palette, "GIF", dict(gif, append_images=[flipped(palette)], disposal=2, **animation)),
        ("photo.tiff", rgb, "TIFF", dict(tiff, icc_profile=PROFILE)),
        ("lzw.tif", rgb, "TIFF", dict(tiff, compression="tiff_lzw")),
        ("zip.tif", rgb, "TIFF", dict(tiff, compression="tiff_adobe_deflate")),
        ("jpeg-compressed.tif", rgb, "TIFF", dict(tiff, compression="jpeg")),
        ("16bit.tif", gray16, "TIFF", tiff),
        ("alpha.tif", rgba, "TIFF", tiff),
        ("cmyk.tif", rgb.convert("CMYK"), "TIFF", tiff),
        ("pages.tiff", rgb, "TIFF", {"save_all": True, "append_images": [flipped(rgb)],
                                     "tiffinfo": {315: MARKER, 274: 6}}),
        ("photo.bmp", rgb, "BMP", {}),
        ("mono.bmp", rgb.convert("1"), "BMP", {}),
        ("gray.bmp", rgb.convert("L"), "BMP", {}),
        ("palette.bmp", rgb.quantize(colors=32), "BMP", {}),
        ("wrong-extension.jpg", rgb, "TIFF", {}),
    ]


def add_with_exiftool(path):
    """The metadata other programs write: XMP, EXIF, GPS; for TIFF pages, the second page too."""
    tags = ["-XMP-dc:Creator=" + MARKER, "-EXIF:Artist=" + MARKER, "-GPSLatitude=31.23", "-GPSLongitude=121.47",
            "-GPSLatitudeRef=N", "-GPSLongitudeRef=E", "-DateTimeOriginal=" + DATE]
    if path.name == "pages.tiff":
        tags += ["-IFD1:Artist=" + MARKER, "-IFD1:Orientation#=3"]
    # -m: Pillow's animated WebP and AVIF files draw minor warnings from ExifTool.
    result = exiftool_run(["-m", "-overwrite_original", *tags], path)
    if result.returncode:
        raise AssertionError(result.stderr.decode())


def exiftool_run(arguments, path):
    """ExifTool on a file whose path may not be ASCII. Windows passes command
    lines in its legacy code page; a UTF-8 argument file reaches ExifTool intact."""
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".args", delete=False) as argfile:
        argfile.write("\n".join([*arguments, str(path)]) + "\n")
    try:
        return subprocess.run([EXIFTOOL, "-config", "", "-charset", "filename=UTF8", "-@", argfile.name],
                              capture_output=True)
    finally:
        os.unlink(argfile.name)


def frames(data):
    """What a viewer shows: every frame's pixels and timing, and the repeat count."""
    with Image.open(io.BytesIO(data)) as picture:
        count = getattr(picture, "n_frames", 1)
        shown = [(picture.size, count, picture.info.get("loop"), picture.info.get("default_image", False))]
        for index in range(count):
            picture.seek(index)
            picture.load()
            shown.append((picture.size, picture.info.get("duration", 0), picture.convert("RGBA").tobytes()))
        return shown


def colors(data):
    """A row of colors converted to Lab through the file's profile, or None."""
    with Image.open(io.BytesIO(data)) as picture:
        profile = picture.info.get("icc_profile")
    if not profile:
        return None
    sample = Image.frombytes("RGB", (256, 1), bytes(range(256)) * 3)
    return ImageCms.profileToProfile(sample, ImageCms.ImageCmsProfile(io.BytesIO(profile)),
                                     ImageCms.createProfile("LAB"), outputMode="LAB").tobytes()


def private_fields(data):
    """The EXIF fields Pillow finds that say who, when or where."""
    with Image.open(io.BytesIO(data)) as picture:
        tags = picture.getexif()
        found = {tag for tag in (ARTIST, DATE_TIME) if tag in tags}
        if DATE_TIME_ORIGINAL in tags.get_ifd(EXIF_IFD):
            found.add(DATE_TIME_ORIGINAL)
        if tags.get_ifd(GPS_IFD):
            found.add(GPS_IFD)
        return found


def image_data_hash(path):
    return exiftool_run(["-api", "ImageHashType=SHA256", "-s3", "-ImageDataHash"], path).stdout


class FormatTests(unittest.TestCase):
    def test_every_variant_is_cleaned_and_looks_the_same(self):
        with tempfile.TemporaryDirectory(prefix="formats-") as temp:
            folder = Path(temp, "常见 格式 %d")
            folder.mkdir()
            for name, image, kind, options in samples():
                with self.subTest(name):
                    source = folder / name
                    image.save(source, kind, **options)
                    if SECOND_CHECK and kind not in ("GIF", "BMP") and name != "wrong-extension.jpg":
                        add_with_exiftool(source)
                    original = source.read_bytes()
                    output = core.clean(str(source), SECOND_CHECK)
                    cleaned = output.read_bytes()
                    self.assertEqual(source.read_bytes(), original)
                    self.assertEqual(frames(cleaned), frames(original))
                    self.assertEqual(colors(cleaned), colors(original))
                    for private in (MARKER.encode(), DATE.encode(), b"dc:creator"):
                        self.assertNotIn(private, cleaned)
                    self.assertEqual(private_fields(cleaned), set())
                    expected = ".png" if kind == "BMP" else ".tiff" if name == "wrong-extension.jpg" else source.suffix
                    self.assertEqual(output.suffix, expected)
                    if SECOND_CHECK and kind not in ("GIF", "BMP"):
                        self.assertEqual(image_data_hash(output), image_data_hash(source))


if __name__ == "__main__":
    unittest.main()
