"""Known identity markers across ICC containers; verify colors with LittleCMS."""
from pathlib import Path
from PIL import Image, ImageCms
import base64
import io
import json
import struct
import subprocess
import tempfile
import unittest

from magicdispel import core
from magicdispel.privacy import PrivacyError, sanitize_icc, sanitize_adaptive_curve

MARKER = 'MD_ICC_USER'
ET = core.find_exiftool()


def fixture_profile(version=4, padded=False):
    original = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    tags = {}
    for i in range(struct.unpack_from('>I', original, 128)[0]):
        tag, offset, size = struct.unpack_from('>4sII', original, 132 + i * 12)
        tags[tag] = original[offset:offset + size]
    if version == 4:
        encoded = MARKER.encode('utf-16be')
        tags[b'desc'] = b'mluc' + b'\0' * 4 + struct.pack('>II', 1, 12) + b'enUS' + struct.pack('>II', len(encoded), 28) + encoded
    else:
        ascii_text = MARKER.encode() + b'\0'
        unicode_text = (MARKER + '\0').encode('utf-16be')
        tags[b'desc'] = (b'desc' + b'\0' * 4 + struct.pack('>I', len(ascii_text)) + ascii_text
                         + struct.pack('>II', 0, len(unicode_text) // 2) + unicode_text + b'\0' * 70)
        tags[b'cprt'] = b'text' + b'\0' * 4 + ascii_text
        for tag in (b'rTRC', b'gTRC', b'bTRC'):
            tags[tag] = b'curv' + b'\0' * 4 + struct.pack('>IH', 1, round(2.2 * 256))
    # Metadata dictionary plus calibration time must not survive as hidden bytes.
    tags[b'calt'] = b'dtim' + b'\0' * 4 + struct.pack('>6H', 2026, 9, 23, 4, 30, 19)
    profile = bytearray(original[:128] + struct.pack('>I', len(tags)) + b'\0' * (12 * len(tags)))
    profile[8] = version
    profile[24:36] = struct.pack('>6H', 2026, 9, 23, 4, 30, 19)
    profile[4:8] = b'USER'
    profile[48:56] = b'USERMODL'
    profile[80:84] = b'USER'
    profile[84:100] = b'PRIVATEPROFILEID'
    for i, (tag, value) in enumerate(tags.items()):
        offset = len(profile)
        profile += value + b'\0' * (-len(value) % 4)
        struct.pack_into('>4sII', profile, 132 + 12 * i, tag, offset, len(value))
    if padded:
        profile += (MARKER.encode() + b'\0') * 6000
    struct.pack_into('>I', profile, 0, len(profile))
    return bytes(profile)


def detected_profiles(path):
    r = subprocess.run([ET, '-config', '', '-j', '-b', '-a', '-ee3', '-G0:1:3:4', '-ICC_Profile', str(path)],
                       check=True, capture_output=True, text=True)
    return [base64.b64decode(v[7:]) for k, v in json.loads(r.stdout)[0].items()
            if k.endswith(':ICC_Profile') and v.startswith('base64:')]


def colors(profile):
    sample = Image.frombytes('RGB', (256, 1), bytes(range(256)) * 3)
    return ImageCms.profileToProfile(sample, ImageCms.ImageCmsProfile(io.BytesIO(profile)),
                                     ImageCms.createProfile('LAB'), outputMode='LAB').tobytes()


class ProfilePrivacyTests(unittest.TestCase):
    def test_hdr_identifier_removed_without_changing_curve(self):
        value = bytearray(230)
        value[:4] = b'gmap'
        struct.pack_into('>6I', value, 12, 230, 98, 106, 106, 106, 0)
        for offset in (36, 44, 52):
            struct.pack_into('>II', value, offset, 150, 80)
        value[60:64] = b'A2B0'
        value[98:106] = b'\x01\x00\x08\x0c\0\0\0\0'
        value[106:122] = b'TESTGUID01234567'
        clean = sanitize_adaptive_curve(bytes(value))
        self.assertEqual(clean[106:122], b'\0' * 16)
        self.assertEqual(clean[:106], value[:106])
        self.assertEqual(clean[122:], value[122:])
        value[20:24] = b'\0\0\0\1'
        with self.assertRaises(PrivacyError):
            sanitize_adaptive_curve(bytes(value))

    def test_identity_removed_from_all_profile_containers(self):
        with tempfile.TemporaryDirectory(prefix='icc-privacy-') as tmp:
            root = Path(tmp)
            image = Image.new('RGB', (32, 24), (71, 113, 219))
            profile = fixture_profile()
            profile_path = root / 'private.icc'
            profile_path.write_bytes(profile)
            cases = [('JPEG', 'jpg'), ('PNG', 'png'), ('WEBP', 'webp'), ('AVIF', 'avif'), ('TIFF', 'tif'), ('GIF', 'gif')]
            for fmt, ext in cases:
                with self.subTest(format=fmt):
                    source = root / ('photo.' + ext)
                    if fmt == 'GIF':
                        image.save(source, fmt, save_all=True, append_images=[image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)] ,duration=100)
                        subprocess.run([ET, '-config', '', '-overwrite_original', '-ICC_Profile<=' + str(profile_path), str(source)],
                                       check=True, capture_output=True)
                    else:
                        image.save(source, fmt, icc_profile=profile)
                    before = detected_profiles(source)
                    self.assertEqual(len(before), 1)
                    output = core.clean(ET, str(source))
                    after = detected_profiles(output)
                    self.assertEqual(len(after), 1)
                    self.assertEqual(colors(before[0]), colors(after[0]))
                    self.assertNotIn(MARKER.encode(), output.read_bytes())
                    self.assertNotIn(MARKER.encode('utf-16be'), after[0])
                    self.assertNotIn(b'PRIVATEPROFILEID', after[0])
                    fields = json.loads(subprocess.check_output([ET, '-config', '', '-j', '-s', '-n', str(output)]))[0]
                    self.assertEqual(fields['ProfileDateTime'], '2000:01:01 00:00:00')
                    self.assertEqual(fields['ProfileDescription'], 'Clean')
                    self.assertNotIn('CalibrationDateTime', fields)

    def test_multipart_jpeg_and_v2_unicode_and_dead_padding(self):
        with tempfile.TemporaryDirectory(prefix='icc-multipart-') as tmp:
            source = Path(tmp) / 'large-profile.jpg'
            profile = fixture_profile(version=2, padded=True)
            Image.new('RGB', (32, 24), (56, 91, 112)).save(source, icc_profile=profile)
            self.assertGreater(source.read_bytes().count(b'ICC_PROFILE\0'), 1)
            output = core.clean(ET, str(source))
            cleaned = detected_profiles(output)[0]
            self.assertEqual(colors(profile), colors(cleaned))
            self.assertNotIn(MARKER.encode(), output.read_bytes())
            self.assertNotIn(MARKER.encode('utf-16be'), cleaned)

    def test_profiles_in_each_tiff_page(self):
        with tempfile.TemporaryDirectory(prefix='icc-pages-') as tmp:
            source = Path(tmp) / 'pages.tif'
            p1, p2 = fixture_profile(4), fixture_profile(2)
            first = Image.new('RGB', (32, 24), 'red')
            second = Image.new('RGB', (32, 24), 'blue')
            first.info['icc_profile'] = p1
            second.info['icc_profile'] = p2
            first.save(source, save_all=True, append_images=[second])
            before = detected_profiles(source)
            self.assertEqual(len(before), 2)
            output = core.clean(ET, str(source))
            after = detected_profiles(output)
            self.assertEqual(len(after), 2)
            for a, b in zip(before, after):
                self.assertEqual(colors(a), colors(b))
                self.assertNotIn(MARKER.encode('utf-16be'), b)

    def test_corrupt_icc_ranges_are_rejected(self):
        broken = bytearray(fixture_profile())
        struct.pack_into('>I', broken, 140, len(broken) + 100)
        with self.assertRaises(PrivacyError):
            sanitize_icc(bytes(broken))
        with self.assertRaises(PrivacyError):
            sanitize_icc(b'not a profile')


if __name__ == '__main__':
    unittest.main()
