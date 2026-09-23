from pathlib import Path
from PIL import Image, ImageCms
import io
import json
import unittest
import struct
import subprocess
import tempfile

from magicdispel import core
from magicdispel.errors import FormatError
from magicdispel.formats import jpeg
API = vars(core)
ET = API['find_exiftool']()


def metadata(path, *options):
    return json.loads(subprocess.check_output([ET, '-config', '', '-j', '-G0:1:4', '-a', '-n', '-ee', *options, str(path)]))[0]


def exif(*args):
    result = subprocess.run([ET, '-config', '', *map(str, args)], capture_output=True, check=True)
    assert not result.stderr, result.stderr
    return result.stdout


def frame_pixels(data):
    with Image.open(io.BytesIO(data)) as image:
        return image.size, image.convert('RGB').tobytes()


class MPFTests(unittest.TestCase):
    def test_frames_and_privacy(self):
        with tempfile.TemporaryDirectory(prefix='mpf-tests-') as temp:
            root = Path(temp)
            for count in (2, 3):
                source = root / ('multi-' + str(count) + '.jpg')
                frames = [Image.new('RGB', (48 - n*8, 32 - n*4), (20+n*80, 130, 70)) for n in range(count)]
                tags = Image.Exif()
                tags[315] = 'PRIVATE_MPF_SENTINEL'
                tags[305] = 'PRIVATE_MPF_SENTINEL'
                frames[0].save(source, 'MPO', save_all=True, append_images=frames[1:], exif=tags,
                               icc_profile=ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes())
                if count == 3:
                    # Pillow 11.3 writes a cumulative size for its third frame. Correct
                    # this test fixture using the independently reported file offsets.
                    fixture = bytearray(source.read_bytes())
                    info = metadata(source)
                    mpf = fixture.index(b'MPF\0') + 4
                    endian = '<' if fixture[mpf:mpf+2] == b'II' else '>'
                    directory = mpf + struct.unpack_from(endian+'I', fixture, mpf+4)[0]
                    for n in range(struct.unpack_from(endian+'H', fixture, directory)[0]):
                        tag, kind, size, pointer = struct.unpack_from(endian+'HHII', fixture, directory+2+12*n)
                        if tag == 0xb002:
                            start = next(v for k,v in info.items() if k.startswith('MPF:MPImage3:') and k.endswith(':MPImageStart'))
                            struct.pack_into(endian+'I', fixture, mpf+pointer+32+4, len(fixture)-start)
                    source.write_bytes(fixture)
                original = source.read_bytes()
                before = metadata(source)
                output = API['clean'](ET, str(source))
                assert source.read_bytes() == original
                after = metadata(output)
                assert API['value_for'](after, 'NumberOfImages') == count
                assert b'PRIVATE_MPF_SENTINEL' not in output.read_bytes()
                assert not any(key.endswith((':Artist', ':Software')) for key in after)
                assert frame_pixels(original) == frame_pixels(output.read_bytes())
                for index in range(2, count+1):
                    old = exif('-b', '-MPImage' + str(index), source)
                    new = exif('-b', '-MPImage' + str(index), output)
                    assert old and new and frame_pixels(old) == frame_pixels(new)
                    assert jpeg.coding_hash(old) == jpeg.coding_hash(new)
                # Independent ExifTool decoder must find every index and extract every frame.
                assert not any(key.endswith((':Warning', ':Error')) for key in after)
                existing = output.read_bytes()
                again = API['clean'](ET, str(source))
                assert again != output and output.read_bytes() == existing
                print('PASS: Pillow-generated', count, 'image MPF; all frames, ICC, privacy and collision handling', flush=True)
        
            # Corrupt an independently generated MPF index. It must never produce a partial output.
            broken = bytearray((root / 'multi-2.jpg').read_bytes())
            mpf = broken.index(b'MPF\0')
            endian = '<' if broken[mpf+4:mpf+6] == b'II' else '>'
            tiff = mpf + 4
            directory = tiff + struct.unpack_from(endian+'I', broken, tiff+4)[0]
            count = struct.unpack_from(endian+'H', broken, directory)[0]
            for n in range(count):
                tag, kind, size, pointer = struct.unpack_from(endian+'HHII', broken, directory+2+12*n)
                if tag == 0xb002:
                    struct.pack_into(endian+'I', broken, tiff+pointer+16+8, 0xfffffff0)
            corrupt = root / 'corrupt.jpg'
            corrupt.write_bytes(broken)
            try:
                API['clean'](ET, str(corrupt))
            except FormatError:
                pass
            else:
                raise AssertionError('Invalid MPF offset accepted')
            assert not (root / 'corrupt_clean.jpg').exists()
            assert not list(root.glob('.magicdispel-*'))
            print('PASS: malformed offsets rejected without output or leftover staging files', flush=True)
        
        print('ALL MPF REGRESSION TESTS PASSED', flush=True)
