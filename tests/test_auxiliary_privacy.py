"""Synthetic HEIF dependency fixtures and real CLI filename checks."""
from pathlib import Path
import hashlib
import json
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from magicdispel import core
from magicdispel.privacy import (PrivacyError, bmff_box, heif_layout,
    heif_auxiliary_types, strip_heif_auxiliary, strip_heif_private)


def fixture(items, refs=(), aux=None, associations=None, primary=1, idat=False,
            extra_props=(), overlap=False, group=()):
    """Build a small item-based file; payloads are test markers, not HEVC images."""
    aux = aux or {}
    properties = [bmff_box(b'ispe', b'\0'*4 + struct.pack('>II', 32, 24))]
    assoc = {ident: [1] for ident, kind, _, _ in items if kind not in {b'mime', b'uri ', b'Exif'}}
    for ident, kind in aux.items():
        properties.append(bmff_box(b'auxC', b'\0'*4 + kind + b'\0'))
        assoc.setdefault(ident, []).append(len(properties))
    properties.extend(extra_props)
    assoc.update(associations or {})
    ipma = b'\0'*4 + len(assoc).to_bytes(4, 'big')
    for ident, values in assoc.items():
        ipma += ident.to_bytes(2, 'big') + bytes([len(values)]) + bytes(values)
    iprp = bmff_box(b'iprp', bmff_box(b'ipco', b''.join(properties)) + bmff_box(b'ipma', ipma))
    infos = []
    for ident, kind, payload, name in items:
        entry = b'\x02\0\0\0' + struct.pack('>HH', ident, 0) + kind + name + b'\0'
        if kind == b'mime': entry += b'application/rdf+xml\0\0'
        if kind == b'uri ': entry += b'urn:test:private\0'
        infos.append(bmff_box(b'infe', entry))
    iinf = bmff_box(b'iinf', b'\0'*4 + len(infos).to_bytes(2, 'big') + b''.join(infos))
    iref = bmff_box(b'iref', b'\0'*4 + b''.join(
        bmff_box(kind, origin.to_bytes(2, 'big') + len(targets).to_bytes(2, 'big')
                 + b''.join(t.to_bytes(2, 'big') for t in targets)) for kind, origin, targets in refs))
    ftyp = bmff_box(b'ftyp', b'heic\0\0\0\0heicmif1')
    payload = b''.join(value for _, _, value, _ in items)
    grpl = bmff_box(b'grpl', bmff_box(b'altr', b'\0'*4 + struct.pack('>II', 1, len(group))
                                      + b''.join(i.to_bytes(4, 'big') for i in group))) if group else b''

    def meta(base):
        entries, offset = [], 0
        for ident, _, value, _ in items:
            absolute = base + (0 if overlap and ident == 2 else offset)
            head = ident.to_bytes(2, 'big') + (b'\0\1' if idat else b'') + b'\0\0\0\1'
            entries.append(head + struct.pack('>II', absolute, len(value)))
            offset += len(value)
        locations = bmff_box(b'iloc', bytes([int(idat), 0, 0, 0, 0x44, 0])
                             + len(items).to_bytes(2, 'big') + b''.join(entries))
        return bmff_box(b'meta', b'\0'*4 + bmff_box(b'pitm', b'\0'*4 + primary.to_bytes(2, 'big'))
                        + iinf + locations + iprp + iref + (bmff_box(b'idat', payload) if idat else b'') + grpl)
    first = meta(0)
    return ftyp + meta(0 if idat else len(ftyp) + len(first) + 8) + (b'' if idat else bmff_box(b'mdat', payload))


class AuxiliaryPrivacyTests(unittest.TestCase):
    def test_remove_auxiliary_payloads_and_keep_display(self):
        # XMP keeps only recognized HDR fields: item 7 has none and goes; item 8
        # keeps its gain-map headroom but loses the private field.
        xmp = b'<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="SECRET_TOOL"><d xmlns="urn:test">1</d></x:xmpmeta>'
        hdr_xmp = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="SECRET_TOOL"><rdf:RDF '
                   b'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description '
                   b'xmlns:HDRGainMap="http://ns.apple.com/HDRGainMap/1.0/" HDRGainMap:HDRGainMapHeadroom="2.5">'
                   b'<d xmlns="urn:test">PRIVATE_FIELD</d></rdf:Description></rdf:RDF></x:xmpmeta>')
        items = [(1,b'hvc1',b'PRIMARY_PIXELS',b'PRIVATE_NAME'),
                 (2,b'hvc1',b'DEPTH_PIXELS',b''), (3,b'mime',b'PRIVATE_CALIBRATION',b''),
                 (4,b'hvc1',b'THUMBNAIL_PIXELS',b''), (5,b'hvc1',b'HDR_PIXELS',b''),
                 (6,b'hvc1',b'ALPHA_PIXELS',b''), (7,b'mime',xmp,b''), (8,b'mime',hdr_xmp,b'')]
        refs = [(b'auxl',2,[1]),(b'cdsc',3,[2]),(b'thmb',4,[1]),(b'auxl',5,[1]),
                (b'auxl',6,[1]),(b'cdsc',7,[5]),(b'cdsc',8,[5])]
        aux = {2:b'urn:mpeg:hevc:2015:auxid:2',5:b'urn:com:apple:photo:2020:aux:hdrgainmap',
               6:b'urn:mpeg:hevc:2015:auxid:1'}
        for use_idat in (False, True):
            with self.subTest(idat=use_idat):
                original = fixture(items, refs, aux, idat=use_idat,
                                   extra_props=[bmff_box(b'abcd',b'UNUSED_PRIVATE_PROPERTY')])
                cleaned = strip_heif_auxiliary(original)
                self.assertEqual(len(cleaned),len(original))
                self.assertEqual(strip_heif_auxiliary(cleaned),cleaned)
                self.assertEqual(set(heif_layout(cleaned)['items']),{1,5,6,8})
                for marker in [b'DEPTH_PIXELS',b'PRIVATE_CALIBRATION',b'THUMBNAIL_PIXELS',
                               b'PRIVATE_NAME',b'SECRET_TOOL',b'UNUSED_PRIVATE_PROPERTY',
                               b'urn:test',b'PRIVATE_FIELD']:
                    self.assertNotIn(marker,cleaned)
                for marker in [b'PRIMARY_PIXELS',b'HDR_PIXELS',b'ALPHA_PIXELS',
                               b'HDRGainMap:HDRGainMapHeadroom="2.5"']:
                    self.assertIn(marker,cleaned)
                self.assertEqual(heif_layout(original)['idat'],heif_layout(cleaned)['idat'])

    def test_shared_tiles_and_metadata_references(self):
        items = [(1,b'grid',b'MAIN_GRID',b''),(2,b'grid',b'DEPTH_GRID',b''),
                 (3,b'hvc1',b'SHARED_TILE',b''),(4,b'hvc1',b'PRIVATE_TILE',b''),
                 (5,b'Exif',b'SHARED_METADATA',b'')]
        refs = [(b'dimg',1,[3]),(b'dimg',2,[3,4]),(b'auxl',2,[1]),(b'cdsc',5,[1,2])]
        original = fixture(items,refs,{2:b'urn:mpeg:hevc:2015:auxid:2'})
        cleaned = strip_heif_auxiliary(original)
        self.assertEqual(set(heif_layout(cleaned)['items']),{1,3,5})
        self.assertIn((b'cdsc',5,[1]),heif_layout(cleaned)['references'])
        self.assertNotIn(b'PRIVATE_TILE',cleaned)
        self.assertIn(b'SHARED_TILE',cleaned)

    def test_dangerous_layouts_are_rejected(self):
        items=[(1,b'hvc1',b'PRIMARY_DATA',b''),(2,b'hvc1',b'DEPTH_DATA',b'')]
        depth={2:b'urn:mpeg:hevc:2015:auxid:2'}
        bad=[fixture(items,[(b'dimg',1,[2])],depth),
             fixture(items,[(b'auxl',2,[1])],depth,overlap=True),
             fixture(items,[(b'auxl',2,[1])],depth,group=[1,2]),
             fixture(items,[(b'auxl',2,[1])],{2:b'urn:unknown:aux'}),
             fixture(items,[(b'abcd',1,[2])],depth),
             fixture(items,[(b'auxl',2,[99])],depth),
             fixture(items,[(b'auxl',2,[1])],depth,associations={1:[99]})]
        for index,value in enumerate(bad):
            with self.subTest(case=index), self.assertRaises(PrivacyError):
                strip_heif_auxiliary(value)

    def test_private_uri_and_auxiliary_cleanup_compose(self):
        original=fixture([(1,b'hvc1',b'PRIMARY_DATA',b''),(2,b'uri ',b'PRIVATE_PLIST',b'')],
                         [(b'cdsc',2,[1])])
        cleaned=strip_heif_auxiliary(strip_heif_private(original))
        self.assertNotIn(b'PRIVATE_PLIST',cleaned)
        self.assertEqual(set(heif_layout(cleaned)['items']),{1})

    def test_no_depth_or_toolkit_allowlist_escape(self):
        for group,tag,value in [('XMP-depthData','IntrinsicMatrix',[1]*9),
                                ('XMP-depthBlurEffect','RenderingParameters','UkVORA=='),
                                ('XMP-x','XMPToolkit','XMP Core 6.0.0')]:
            with self.subTest(tag=tag), self.assertRaises(core.CleanError):
                core.verify_metadata({'File:FileType':'HEIC','XMP:'+group+':'+tag:value})


class AnonymousNameTests(unittest.TestCase):
    def test_cli_names_originals_and_collisions(self):
        with tempfile.TemporaryDirectory(prefix='anonymous-') as temp:
            root=Path(temp)/'中文 空格';root.mkdir()
            source=root/'姓名_2026-09-23.JPG'
            Image.new('RGB',(24,32),'blue').save(source)
            original=source.read_bytes()
            r=subprocess.run([sys.executable,'-m','magicdispel','--anonymous',str(source)],
                             capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stderr)
            targets=list(root.glob('photo_*.jpg'))
            self.assertEqual(len(targets),1)
            self.assertRegex(targets[0].name,r'^photo_[0-9a-f]{32}\.jpg$')
            self.assertEqual(source.read_bytes(),original)
            collision=root/('photo_'+'a'*32+'.jpg');collision.write_bytes(b'KEEP_EXISTING')
            with patch.object(core.secrets,'token_hex',side_effect=['a'*32,'b'*32]):
                out=core.publish(targets[0].read_bytes(),source,'.JPG',anonymous=True)
            self.assertEqual(out.name,'photo_'+'b'*32+'.jpg')
            self.assertEqual(collision.read_bytes(),b'KEEP_EXISTING')
            self.assertEqual(out.read_bytes(),targets[0].read_bytes())
            bmp=root/'姓名.bmp';Image.new('RGB',(8,8),'red').save(bmp)
            out=core.clean(core.find_exiftool(),str(bmp),anonymous=True)
            self.assertRegex(out.name,r'^photo_[0-9a-f]{32}\.png$')


if __name__=='__main__':
    unittest.main()
