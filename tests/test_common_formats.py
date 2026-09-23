"""Synthetic regression fixtures; never use personal photographs in this suite."""
from pathlib import Path
from PIL import Image, ImageCms, PngImagePlugin
import unittest
import io
import subprocess
import tempfile

from magicdispel import core, exiftool
from magicdispel.errors import VerificationError
ET = exiftool.find()


def edit(path, *tags):
    result = subprocess.run([ET, '-config', '', '-overwrite_original', *tags, str(path)],
                            check=True, capture_output=True)
    assert not result.stderr, result.stderr


def pixels(path):
    with Image.open(path) as picture:
        count = getattr(picture, 'n_frames', 1)
        header = (picture.size, count, picture.info.get('loop'), picture.info.get('default_image', False))
        frames = []
        for index in range(count):
            picture.seek(index)
            picture.load()
            frames.append((picture.size, picture.info.get('duration', 0), picture.convert('RGBA').tobytes()))
        return header, frames


def image_data_hash(path):
    return subprocess.check_output([ET, '-config', '', '-api', 'ImageHashType=SHA256', '-s3',
                                    '-ImageDataHash', str(path)])


def icc(path):
    return subprocess.check_output([ET, '-config', '', '-b', '-ICC_Profile', str(path)])


def icc_colors(profile):
    if not profile:
        return None
    sample = Image.frombytes('RGB', (256, 1), bytes(range(256)) * 3)
    converted = ImageCms.profileToProfile(sample,
        ImageCms.ImageCmsProfile(io.BytesIO(profile)), ImageCms.createProfile('LAB'), outputMode='LAB')
    return converted.tobytes()


class CommonFormatTests(unittest.TestCase):
    def test_formats_and_privacy(self):
        with tempfile.TemporaryDirectory(prefix='formats-') as temp:
            root = Path(temp) / '常见 格式 %d'
            root.mkdir()
            rgb = Image.new('RGB', (65, 49))
            rgb.putdata([((x*17)%256, (y*11)%256, ((x+y)*7)%256) for y in range(49) for x in range(65)])
            rgba = rgb.convert('RGBA')
            rgba.putalpha(Image.linear_gradient('L').resize(rgb.size))
            palette = rgba.quantize(colors=32)
            gray16 = Image.frombytes('I;16', (65,49), bytes((i*7)%256 for i in range(65*49*2)))
            profile = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
            pnginfo = PngImagePlugin.PngInfo()
            pnginfo.add_text('Comment', 'PRIVATE_MARKER')
            cases = [
                ('rgb.jpg', rgb, 'JPEG', {}),
                ('progressive.JPG', rgb, 'JPEG', {'progressive': True}),
                ('gray.jpeg', rgb.convert('L'), 'JPEG', {}),
                ('cmyk.jpg', rgb.convert('CMYK'), 'JPEG', {}),
                ('rgb.png', rgb, 'PNG', {'pnginfo': pnginfo}),
                ('alpha.png', rgba, 'PNG', {'pnginfo': pnginfo}),
                ('palette.png', palette, 'PNG', {'pnginfo': pnginfo}),
                ('16bit.png', gray16, 'PNG', {'pnginfo': pnginfo}),
                ('rgb.webp', rgb, 'WEBP', {}),
                ('lossless.webp', rgb, 'WEBP', {'lossless': True}),
                ('alpha.webp', rgba, 'WEBP', {'lossless': True}),
                ('rgb.avif', rgb, 'AVIF', {}),
                ('animated.avif', rgb, 'AVIF', {'save_all': True, 'append_images': [rgb.transpose(Image.Transpose.FLIP_LEFT_RIGHT)], 'duration':[100,250], 'loop':3}),
                ('photo.gif', rgb, 'GIF', {'comment': b'PRIVATE_MARKER'}),
                ('alpha.gif', palette, 'GIF', {'comment': b'PRIVATE_MARKER'}),
                ('photo.bmp', rgb, 'BMP', {}),
                ('mono.bmp', rgb.convert('1'), 'BMP', {}),
                ('gray.bmp', rgb.convert('L'), 'BMP', {}),
                ('palette.bmp', rgb.quantize(colors=32), 'BMP', {}),
                ('photo.tiff', rgb, 'TIFF', {'tiffinfo': {315: 'PRIVATE_MARKER'}}),
                ('lzw.tif', rgb, 'TIFF', {'compression': 'tiff_lzw', 'tiffinfo': {270:'PRIVATE_MARKER'}}),
                ('zip.tif', rgb, 'TIFF', {'compression': 'tiff_adobe_deflate'}),
                ('jpeg-compressed.tif', rgb, 'TIFF', {'compression': 'jpeg'}),
                ('16bit.tif', gray16, 'TIFF', {}),
                ('alpha.tif', rgba, 'TIFF', {}),
                ('cmyk.tif', rgb.convert('CMYK'), 'TIFF', {}),
                ('pages.tiff', rgb, 'TIFF', {'save_all': True, 'append_images': [rgb.transpose(Image.Transpose.FLIP_LEFT_RIGHT)], 'tiffinfo': {315:'PRIVATE_MARKER',274:6}}),
                ('animated.gif', palette, 'GIF', {'save_all': True, 'append_images': [palette.transpose(Image.Transpose.FLIP_LEFT_RIGHT)], 'duration':[100,250], 'disposal':2, 'loop':3, 'comment':b'PRIVATE_MARKER'}),
                ('animated.webp', rgba, 'WEBP', {'save_all': True, 'append_images': [rgba.transpose(Image.Transpose.FLIP_LEFT_RIGHT)], 'duration':[100,250], 'loop':3}),
                ('animated.png', rgba, 'PNG', {'save_all': True, 'append_images':[rgba.transpose(Image.Transpose.FLIP_LEFT_RIGHT)], 'duration':[100,250], 'loop':3, 'pnginfo':pnginfo}),
                ('poster.apng', rgba, 'PNG', {'save_all': True, 'append_images':[rgba.transpose(Image.Transpose.FLIP_LEFT_RIGHT), rgba], 'duration':[100,250], 'loop':2, 'default_image':True, 'pnginfo':pnginfo}),
                ('wrong-extension.jpg', rgb, 'TIFF', {}),
            ]
            failures = []
            for name, image, fmt, options in cases:
                source = root / name
                try:
                    image.save(source, fmt, **options)
                    if fmt in {'JPEG','PNG','WEBP','AVIF','TIFF'} and name != 'wrong-extension.jpg':
                        edit(source, '-XMP-dc:Creator=PRIVATE_MARKER', '-EXIF:Artist=PRIVATE_MARKER',
                             '-GPSLatitude=31.23', '-GPSLongitude=121.47', '-GPSLatitudeRef=N', '-GPSLongitudeRef=E',
                             '-DateTimeOriginal=2020:01:02 03:04:05')
                    if name in {'rgb.jpg','photo.tiff','lossless.webp'}:
                        iccpath = root.parent / 'profile.icc'
                        iccpath.write_bytes(profile)
                        edit(source, '-ICC_Profile<=' + str(iccpath))
                    if name == 'pages.tiff':
                        edit(source, '-IFD1:Artist=PRIVATE_MARKER', '-IFD1:Orientation#=3')
                    original = source.read_bytes()
                    before_pixels = pixels(source)
                    out = core.clean(str(source), ET)
                    after = exiftool.read(ET, out)
                    assert source.read_bytes() == original, 'source modified'
                    assert pixels(out) == before_pixels, 'displayed frame/timing/loop changed'
                    assert icc_colors(icc(source)) == icc_colors(icc(out)), 'ICC color conversion changed'
                    assert b'PRIVATE_MARKER' not in out.read_bytes(), 'private text retained'
                    assert not any(':GPS' in key or key.endswith((':Artist', ':Creator', ':DateTimeOriginal', ':Comment')) for key in after), after
                    if fmt not in {'GIF', 'BMP'}:
                        assert image_data_hash(source) == image_data_hash(out)
                    if fmt == 'BMP':
                        assert out.suffix == '.png'
                    if name == 'wrong-extension.jpg':
                        assert out.suffix == '.tiff'
                    print('PASS', name, flush=True)
                except Exception as error:
                    failures.append((name, str(error)))
                    print('FAIL', name, str(error), flush=True)
            assert not failures, failures
            assert not list(root.glob('.magicdispel-*'))
            # Explicitly catch comment fields reported in the File family.
            try:
                exiftool.check_tags({'File:Comment':'PRIVATE_MARKER'}, 'GIF')
            except VerificationError:
                pass
            else:
                raise AssertionError('File:Comment escaped validation')
            print('ALL 32 COMMON FORMAT CASES PASSED', flush=True)


if __name__ == '__main__':
    unittest.main()
