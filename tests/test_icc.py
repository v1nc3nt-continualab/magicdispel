"""ICC profiles: where they came from is removed, how they convert colors is not."""
import io
import struct
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageCms

from magicdispel import core, icc

MARKER = "MD_ICC_USER"
PLACEHOLDER_DATE = struct.pack(">6H", 2000, 1, 1, 0, 0, 0)
SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def tag_table(profile):
    """{tag: data}, read independently of the code under test."""
    table = {}
    for index in range(struct.unpack_from(">I", profile, 128)[0]):
        tag, offset, size = struct.unpack_from(">4sII", profile, 132 + 12 * index)
        table[tag] = profile[offset:offset + size]
    return table


def assemble(header, tags):
    """A profile from a 128-byte header and {tag: data}, with the size filled in."""
    profile = bytearray(header[:128] + struct.pack(">I", len(tags)) + bytes(12 * len(tags)))
    for index, (tag, value) in enumerate(tags.items()):
        offset = len(profile)
        profile += value + bytes(-len(value) % 4)
        struct.pack_into(">4sII", profile, 132 + 12 * index, tag, offset, len(value))
    struct.pack_into(">I", profile, 0, len(profile))
    return profile


def user_profile(version=4, padded=False):
    """sRGB with the user's name in its description, creator, model and profile
    ID, calibration dates, and optionally dead space full of MARKER."""
    tags = tag_table(SRGB)
    if version == 4:
        text = MARKER.encode("utf-16be")
        tags[b"desc"] = b"mluc" + bytes(4) + struct.pack(">II", 1, 12) + b"enUS" + struct.pack(">II", len(text), 28) + text
    else:
        ascii_text, unicode_text = MARKER.encode() + b"\0", (MARKER + "\0").encode("utf-16be")
        tags[b"desc"] = (b"desc" + bytes(4) + struct.pack(">I", len(ascii_text)) + ascii_text
                         + struct.pack(">II", 0, len(unicode_text) // 2) + unicode_text + bytes(70))
        tags[b"cprt"] = b"text" + bytes(4) + ascii_text
        for tag in (b"rTRC", b"gTRC", b"bTRC"):
            tags[tag] = b"curv" + bytes(4) + struct.pack(">IH", 1, round(2.2 * 256))
    tags[b"calt"] = b"dtim" + bytes(4) + struct.pack(">6H", 2026, 9, 23, 4, 30, 19)
    header = bytearray(SRGB[:128])
    header[8] = version
    header[4:8], header[24:36], header[48:56] = b"USER", struct.pack(">6H", 2026, 9, 23, 4, 30, 19), b"USERMODL"
    header[80:84], header[84:100] = b"USER", b"PRIVATEPROFILEID"
    profile = assemble(header, tags)
    if padded:
        profile += (MARKER.encode() + b"\0") * 6000
        struct.pack_into(">I", profile, 0, len(profile))
    return bytes(profile)


def s15(*values):
    return struct.pack(">%di" % len(values), *(round(value * 65536) for value in values))


def para_curve(function=3, *parameters):
    parameters = parameters or (2.4, 1 / 1.055, 0.055 / 1.055, 1 / 12.92, 0.04045)
    return b"para" + bytes(4) + struct.pack(">HH", function, 0) + s15(*parameters)


def filler(size):
    return bytes(n % 251 + 1 for n in range(size))


def lut16(inputs=3, outputs=3, grid=2, entries=2):
    """A lut16Type (mft2) transform, laid out independently of the code under test."""
    tables = 2 * (entries * inputs + grid ** inputs * outputs + entries * outputs)
    return (b"mft2" + bytes(4) + bytes([inputs, outputs, grid, 0]) + s15(1, 0, 0, 0, 1, 0, 0, 0, 1)
            + struct.pack(">HH", entries, entries) + filler(tables))


def transform(inputs=3, outputs=3, kind=b"mAB ", gap=b""):
    """A lutAToBType or lutBToAType transform with all five elements: B curves,
    a matrix, M curves, a CLUT and A curves. `gap` goes after the B curves."""
    curve = para_curve(0, 2.2)
    b_curves, a_curves = (outputs, inputs) if kind == b"mAB " else (inputs, outputs)
    clut = bytes([2] * inputs + [0] * (16 - inputs)) + bytes([2, 0, 0, 0]) + filler(2 ** inputs * outputs * 2)
    elements = [curve * b_curves + gap, s15(1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0), curve * b_curves, clut,
                curve * a_curves]
    offsets, position = [], 32
    for element in elements:
        offsets.append(position)
        position += len(element) + -len(element) % 4
    body = b"".join(element + bytes(-len(element) % 4) for element in elements)
    return kind + bytes(4) + bytes([inputs, outputs, 0, 0]) + struct.pack(">5I", *offsets) + body


def spread(count, low, high):
    """`count` 16-bit numbers rising from low towards high."""
    return struct.pack(">%dH" % count, *(low + (high - low) * n // count for n in range(count)))


def gain_curve(images=((5000, 4),), common=False, primaries=0, mix=2, white=None, reference_white_tone_map=False,
               count_short=True, **replaced):
    """An HAGC tag: SMPTE ST 2094-50 metadata (Annex C) after the tag type and
    count, written independently of the code under test. `images` are
    (headroom, control points) of each alternate image; with `common`, all
    share the first one's mix and curve. The count is one short of the record,
    as iOS 27 writes it. `replaced` sets bytes of the record: {"at_3": 0xff}."""
    record = bytearray([0, 0x40 | (0x80 if white is not None else 0)])
    record += struct.pack(">H", white) if white is not None else b""
    record += struct.pack(">H", 18000)  # baseline headroom
    if reference_white_tone_map:
        record.append(0x80)
    else:
        record.append(len(images) << 4 | primaries << 2 | common << 1 | common)
        record += struct.pack(">8H", *range(1000, 9000, 1000)) if primaries == 3 else b""
        for index, (headroom, points) in enumerate(images):
            record += struct.pack(">H", headroom)
            if index == 0 or not common:
                record += bytes([mix << 6 | (0b101000 if mix == 3 else 0)])
                record += struct.pack(">HH", 30000, 20000) if mix == 3 else b""
                record += bytes([(points - 1) << 3])
                record += spread(points, 0, 64000)  # x of each control point
            count = images[0][1] if common else points
            record += spread(count, 1000, 60000) + spread(count, 1, 35999)  # y, slope angle
    for key, value in replaced.items():
        record[int(key[3:])] = value
    return b"hagc" + bytes(4) + struct.pack(">I", len(record) - count_short) + bytes(record)


def display_profile(**replaced):
    """Shaped like the profile a macOS screenshot embeds: v2 matrix/TRC colors,
    Apple para curves, and display identity and setup tags that carry MARKER."""
    marker = MARKER.encode()
    name = (MARKER + " Studio Display").encode("utf-16be")
    gamma = b"curv" + bytes(4) + struct.pack(">IH", 1, round(2.2 * 256))
    tags = {
        b"desc": b"desc" + bytes(4) + struct.pack(">I", len(marker) + 1) + marker + bytes(79),
        b"dscm": b"mluc" + bytes(4) + struct.pack(">II", 1, 12) + b"enUS" + struct.pack(">II", len(name), 28) + name,
        b"cprt": b"text" + bytes(4) + b"Copyright " + marker + b"\0",
        b"wtpt": b"XYZ " + bytes(4) + s15(0.9642, 1.0, 0.8249),
        b"rXYZ": b"XYZ " + bytes(4) + s15(0.5143, 0.2411, -0.0011),
        b"gXYZ": b"XYZ " + bytes(4) + s15(0.2922, 0.6929, 0.0418),
        b"bXYZ": b"XYZ " + bytes(4) + s15(0.1577, 0.0660, 0.7841),
        b"rTRC": gamma, b"gTRC": gamma, b"bTRC": gamma,
        b"aarg": para_curve(), b"aagg": para_curve(), b"aabg": para_curve(),
        b"vcgt": b"vcgt" + bytes(4) + struct.pack(">I", 1) + s15(*[1.0, 0.0, 1.0] * 3),
        b"ndin": b"ndin" + bytes(4) + marker.ljust(54, b"\0"),
        b"mmod": b"mmod" + bytes(4) + marker.ljust(32, b"\0"),
        b"vcgp": b"vcgp" + bytes(4) + marker.ljust(48, b"\0"),
    }
    tags.update((tag.encode(), value) for tag, value in replaced.items())
    header = bytearray(128)
    header[4:8], header[8:12], header[12:24] = b"appl", b"\x02\x10\0\0", b"mntrRGB XYZ "
    header[24:36] = struct.pack(">6H", 2026, 9, 15, 12, 7, 6)
    header[36:44], header[48:52], header[80:84] = b"acspAPPL", b"APPL", b"appl"
    header[68:80] = s15(0.9642, 1.0, 0.8249)
    return bytes(assemble(header, tags))


def colors(profile):
    """A row of colors converted to Lab through the profile by LittleCMS."""
    sample = Image.frombytes("RGB", (256, 1), bytes(range(256)) * 3)
    return ImageCms.profileToProfile(sample, ImageCms.ImageCmsProfile(io.BytesIO(profile)),
                                     ImageCms.createProfile("LAB"), outputMode="LAB").tobytes()


def profiles(data):
    """The ICC profile of each page or frame, as Pillow reads it."""
    with Image.open(io.BytesIO(data)) as picture:
        found = []
        for index in range(getattr(picture, "n_frames", 1)):
            picture.seek(index)
            found.append(picture.info.get("icc_profile"))
        return found


def gif_with_profile(image, profile):
    """A GIF with its profile in an ICCRGBG1012 application extension, which
    Pillow neither writes nor reads."""
    buffer = io.BytesIO()
    image.save(buffer, "GIF")
    data = buffer.getvalue()
    start = 13 + (3 << ((data[10] & 7) + 1) if data[10] & 0x80 else 0)  # after the global color table
    pieces = [profile[n:n + 255] for n in range(0, len(profile), 255)]
    extension = b"\x21\xff\x0bICCRGBG1012" + b"".join(bytes([len(piece)]) + piece for piece in pieces) + b"\0"
    return data[:start] + extension + data[start:]


def gif_profile(data):
    """The profile in a GIF's ICCRGBG1012 extension, or None."""
    start = data.find(b"\x21\xff\x0bICCRGBG1012")
    if start < 0:
        return None
    position, pieces = start + 14, []
    while data[position]:
        pieces.append(data[position + 1:position + 1 + data[position]])
        position += 1 + data[position]
    return b"".join(pieces)


class SanitizeTests(unittest.TestCase):
    def assertClean(self, profile, cleaned):
        self.assertEqual(colors(cleaned), colors(profile))
        self.assertNotIn(MARKER.encode(), cleaned)
        self.assertNotIn(MARKER.encode("utf-16be"), cleaned)
        self.assertEqual(cleaned[24:36], PLACEHOLDER_DATE)
        # CMM, platform, manufacturer, model, creator and profile ID are cleared.
        self.assertEqual(cleaned[4:8] + cleaned[40:44] + cleaned[48:56] + cleaned[80:100], bytes(36))
        self.assertEqual(ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(cleaned))).strip(),
                         "Clean")

    def test_user_profiles_of_both_versions(self):
        for version in (2, 4):
            with self.subTest(version=version):
                profile = user_profile(version)
                cleaned = icc.sanitize(profile)
                self.assertClean(profile, cleaned)
                self.assertEqual(len(cleaned), len(profile))
                self.assertNotIn(b"calt", tag_table(cleaned))

    def test_display_profile_keeps_curves_and_drops_display_identity(self):
        profile = display_profile()
        cleaned = icc.sanitize(profile)
        self.assertClean(profile, cleaned)
        before, after = tag_table(profile), tag_table(cleaned)
        for tag in (b"wtpt", b"rXYZ", b"gXYZ", b"bXYZ", b"rTRC", b"gTRC", b"bTRC", b"aarg", b"aagg", b"aabg"):
            self.assertEqual(after[tag], before[tag])
        for tag in (b"dscm", b"mmod", b"ndin", b"vcgt", b"vcgp"):
            self.assertNotIn(tag, after)

    def test_hdr_curve_loses_its_image_identifier_only(self):
        # Laid out like the gmap tags of iPhone HEIC gain maps: one curve of six
        # points shared by the three channels, filling the tag. Up to iOS 26
        # the fields start at byte 98; from iOS 27 at 97.
        for start, primaries in ((98, 12), (97, 12), (97, 9)):
            size, identifier, curve = start + 132, start + 8, start + 52
            value = bytearray(size)
            value[:4] = b"gmap"
            struct.pack_into(">6I", value, 12, size, start, identifier, identifier, identifier, 0)
            for offset in (36, 44, 52):
                struct.pack_into(">II", value, offset, curve, 80)
            value[60:64] = b"A2B0"
            value[start:identifier] = b"\x01\x00\x08" + bytes([primaries]) + bytes(4)
            value[identifier:identifier + 16] = b"TESTGUID01234567"
            value[identifier + 16:curve] = s15(*range(7))
            struct.pack_into(">I", value, curve + 4, 6)
            value[curve + 8:] = s15(*range(18))
            with self.subTest(start=start, primaries=primaries):
                cleaned = icc.sanitize_adaptive_curve(bytes(value))
                self.assertEqual(cleaned[:identifier] + cleaned[identifier + 16:],
                                 value[:identifier] + value[identifier + 16:])
                self.assertEqual(cleaned[identifier:identifier + 16], bytes(16))
            # A header field, the count, a curve that leaves room for more, a
            # layout between the two known ones, and other primaries.
            for offset, replacement in ((20, b"\0\0\0\1"), (curve + 4, struct.pack(">I", 5)),
                                        (40, struct.pack(">I", 72)), (16, struct.pack(">I", start - 2)),
                                        (start, b"\x01\x00\x08\x01")):
                broken = bytearray(value)
                broken[offset:offset + 4] = replacement
                with self.subTest(start=start, primaries=primaries, offset=offset), \
                        self.assertRaises(icc.ProfileError):
                    icc.sanitize_adaptive_curve(bytes(broken))

    def test_gain_curves_are_kept_when_every_bit_is_accounted_for(self):
        # iOS 27 puts an HAGC tag in the profile of an HDR photo's alternate image.
        exact = [gain_curve(), gain_curve(count_short=False), gain_curve(white=20000), gain_curve(primaries=3),
                 gain_curve(mix=3), gain_curve(reference_white_tone_map=True),
                 gain_curve(images=((0, 32), (30000, 2), (50000, 1), (60000, 7))),
                 gain_curve(images=((0, 3), (40000, 3)), common=True)]
        for value in exact:
            with self.subTest(size=len(value)):
                icc.check_gain_curve(value)
                icc.check_gain_curve(value + bytes(3))  # zero padding carries nothing,
                padded = value[:8] + struct.pack(">I", len(value) - 9) + value[12:] + bytes(3)
                icc.check_gain_curve(padded)  # and the count may cover it
        tags = {**tag_table(SRGB), b"HAGC": gain_curve()}
        self.assertEqual(tag_table(icc.sanitize(bytes(assemble(SRGB, tags))))[b"HAGC"], gain_curve())

    def test_gain_curves_holding_anything_else_are_refused(self):
        valid = gain_curve()
        refused = {
            "version": gain_curve(at_0=0x20), "minimum version": gain_curve(at_0=0x04),
            "reserved flags": gain_curve(at_0=0x01), "reserved tone map flags": gain_curve(at_1=0x41),
            "reserved mix bits": gain_curve(at_7=0x81), "reserved curve bits": gain_curve(at_8=0x19),
            "five alternate images": gain_curve(at_4=0x50),
            "headroom out of range": gain_curve(at_2=0xea, at_3=0x61),     # 60001
            "x out of range": gain_curve(at_15=0xfa, at_16=0x01),         # 64001
            "slope angle out of range": gain_curve(at_25=0, at_26=0),     # 0
            "count": valid[:8] + struct.pack(">I", len(valid) - 14) + valid[12:],
            "count past the tag": valid[:8] + struct.pack(">I", len(valid) - 11) + valid[12:],
            "data after the record": valid + b"\1", "truncated": valid[:-1], "type": b"HAGC" + valid[4:],
        }
        for reason, value in refused.items():
            with self.subTest(reason), self.assertRaises(icc.ProfileError):
                icc.check_gain_curve(value)

    def test_color_tags_hold_nothing_but_their_layout(self):
        tags = tag_table(SRGB)
        exact = [tags[b"rTRC"], tags[b"wtpt"], tags[b"chad"], para_curve(), lut16(), lut16(1, 3, 5), transform(),
                 transform(1, 3), transform(3, 1, b"mBA ")]
        for value in exact:
            with self.subTest(kind=value[:4]):
                icc.check_layout(value)
                icc.check_layout(value + bytes(3))  # zero padding carries nothing
        xyz, marker = tags[b"wtpt"], MARKER.encode()
        grid = bytearray(transform())
        grid[struct.unpack_from(">I", grid, 24)[0] + 5] = 7  # a grid size for a fourth input
        for value in (tags[b"rTRC"] + marker, xyz + s15(0.1, 0.2, 0.3), xyz[:4] + b"USER" + xyz[8:],
                      para_curve()[:10] + b"US" + para_curve()[12:], lut16() + marker,
                      transform() + b"\0" + marker, transform(gap=marker[:4]), bytes(grid),
                      b"mpet" + bytes(4) + struct.pack(">HHI", 3, 3, 0)):
            with self.subTest(value=value[:4]), self.assertRaises(icc.ProfileError):
                icc.check_layout(value)

    def test_a_profile_with_data_hidden_in_a_curve_is_refused(self):
        tags = tag_table(SRGB)
        tags[b"rTRC"] += MARKER.encode()
        floating = {**tag_table(SRGB), b"D2B0": b"mpet" + bytes(4) + struct.pack(">HHI", 3, 3, 0)}
        for profile in (bytes(assemble(SRGB, tags)), bytes(assemble(SRGB, floating))):
            with self.subTest(size=len(profile)), self.assertRaises(icc.ProfileError):
                icc.sanitize(profile)

    def test_malformed_apple_curves_are_refused(self):
        for curve in (para_curve(7), para_curve()[:-4], para_curve(0, 2.2, 1.0),
                      b"curv" + bytes(4) + struct.pack(">IH", 1, 563)):
            with self.subTest(curve=curve[:12]), self.assertRaises(icc.ProfileError):
                icc.sanitize(display_profile(aarg=curve))

    def test_damaged_profiles_are_refused(self):
        broken = bytearray(user_profile())
        struct.pack_into(">I", broken, 140, len(broken) + 100)  # a tag reaching past the end
        for profile in (bytes(broken), b"not a profile"):
            with self.subTest(size=len(profile)), self.assertRaises(icc.ProfileError):
                icc.sanitize(profile)


class ContainerTests(unittest.TestCase):
    def test_every_format_carries_the_sanitized_profile(self):
        image, profile = Image.new("RGB", (32, 24), (71, 113, 219)), user_profile()
        with tempfile.TemporaryDirectory(prefix="icc-") as folder:
            for kind, suffix in (("JPEG", "jpg"), ("PNG", "png"), ("WEBP", "webp"), ("AVIF", "avif"),
                                 ("TIFF", "tif"), ("GIF", "gif")):
                with self.subTest(format=kind):
                    source = Path(folder, "photo." + suffix)
                    if kind == "GIF":
                        source.write_bytes(gif_with_profile(image, profile))
                    else:
                        image.save(source, kind, icc_profile=profile)
                    profile_in = gif_profile if kind == "GIF" else (lambda data: profiles(data)[0])
                    self.assertEqual(profile_in(source.read_bytes()), profile)
                    cleaned = core.clean(str(source)).read_bytes()
                    self.assertEqual(profile_in(cleaned), icc.sanitize(profile))
                    self.assertNotIn(MARKER.encode(), cleaned)

    def test_a_profile_split_across_jpeg_segments(self):
        profile = user_profile(version=2, padded=True)
        with tempfile.TemporaryDirectory(prefix="icc-") as folder:
            source = Path(folder, "large profile.jpg")
            Image.new("RGB", (32, 24), (56, 91, 112)).save(source, icc_profile=profile)
            self.assertGreater(source.read_bytes().count(b"ICC_PROFILE\0"), 1)
            cleaned = core.clean(str(source)).read_bytes()
            self.assertEqual(profiles(cleaned), [icc.sanitize(profile)])
            self.assertNotIn(MARKER.encode(), cleaned)

    def test_each_tiff_page_keeps_its_own_profile(self):
        first, second = Image.new("RGB", (32, 24), "red"), Image.new("RGB", (32, 24), "blue")
        first.info["icc_profile"], second.info["icc_profile"] = user_profile(4), user_profile(2)
        with tempfile.TemporaryDirectory(prefix="icc-") as folder:
            source = Path(folder, "pages.tif")
            first.save(source, save_all=True, append_images=[second])
            cleaned = core.clean(str(source)).read_bytes()
            self.assertEqual(profiles(cleaned), [icc.sanitize(user_profile(4)), icc.sanitize(user_profile(2))])

    def test_screenshot_with_a_display_profile(self):
        with tempfile.TemporaryDirectory(prefix="icc-") as folder:
            source = Path(folder, "Screenshot 2026-09-23.png")
            Image.new("RGBA", (32, 24), (40, 120, 200, 255)).save(source, icc_profile=display_profile())
            self.assertEqual(profiles(core.clean(str(source)).read_bytes()), [icc.sanitize(display_profile())])


if __name__ == "__main__":
    unittest.main()
