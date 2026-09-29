"""The XMP a clean file keeps: HDR gain-map fields, each once, their numbers written the one way."""
import unittest

from magicdispel import xmp

HEAD = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        b'<rdf:Description xmlns:hdrgm="http://ns.adobe.com/hdr-gain-map/1.0/" '
        b'xmlns:HDRGainMap="http://ns.apple.com/HDRGainMap/1.0/" ')
TAIL = b'</rdf:Description></rdf:RDF></x:xmpmeta>'


def packet(attributes=b"", children=b"", extra=b""):
    return HEAD + attributes + b">" + children + TAIL + extra


def fields(data):
    return {(name): value for _, name, value in xmp.hdr_fields(data)}


class XMPTests(unittest.TestCase):
    def test_an_encoding_it_does_not_know_is_an_xmp_error_not_a_crash(self):
        for encoding in (b"nonexistent", b"shift_jis", b"utf-32", b"ucs-4", b"x-user-defined"):
            data = b'<?xml version="1.0" encoding="' + encoding + b'"?>' + packet()
            with self.subTest(encoding=encoding), self.assertRaises(xmp.XMPError):
                xmp.hdr_fields(data)

    def test_numbers_are_written_the_one_way(self):
        data = packet(b'hdrgm:Version="1.00" hdrgm:GainMapMax="  2.50 " hdrgm:Gamma="1e0" '
                      b'HDRGainMap:HDRGainMapVersion="0065536"')
        self.assertEqual(fields(data),
                         {"Version": "1.0", "GainMapMax": "2.5", "Gamma": "1.0", "HDRGainMapVersion": "65536"})
        self.assertEqual(fields(xmp.hdr_packet(xmp.hdr_fields(data))), fields(data))  # written and read again

    def test_small_numbers_are_written_without_an_exponent(self):
        data = packet(b'hdrgm:OffsetSDR="0.000010" hdrgm:OffsetHDR="1e-5" hdrgm:GainMapMin="-0.0" '
                      b'hdrgm:GainMapMax="5.5e-11" hdrgm:Gamma="1E2"')
        self.assertEqual(fields(data), {"OffsetSDR": "0.00001", "OffsetHDR": "0.00001", "GainMapMin": "0.0",
                                        "GainMapMax": "0.000000000055", "Gamma": "100.0"})

    def test_text_that_only_float_would_take_is_not_a_number(self):
        # Underscores, other scripts' digits, and digits by the hundred: each a way to carry bytes along.
        for text in (b"1_0.5", "١٢".encode(), b"0." + b"1" * 100, b"0x10", b"1 2", b"nan", b"inf", b"1e99", b"1e-30"):
            with self.subTest(text=text):
                self.assertEqual(fields(packet(b'hdrgm:GainMapMax="' + text + b'"')), {})

    def test_numbers_that_exiftool_reports_are_numbers_too(self):
        for value in (1.2, 3, [0.5, 1, 0.25], "2.5", -4.0):
            with self.subTest(value=value):
                self.assertTrue(xmp.numeric(value))
        for value in (True, float("nan"), float("inf"), 10 ** 400, [1, 2], [1, 2, 3, 4], "1_0", None, [[1]]):
            with self.subTest(value=value):
                self.assertFalse(xmp.numeric(value))

    def test_a_field_counts_once_and_lists_stay_lists(self):
        again = b'<hdrgm:Gamma>3.5</hdrgm:Gamma><hdrgm:Gamma>4.5</hdrgm:Gamma>'
        self.assertEqual(fields(packet(b'hdrgm:Gamma="2.5"', again)), {"Gamma": "2.5"})
        sequence = (b'<hdrgm:GainMapMin><rdf:Seq><rdf:li>0.5</rdf:li><rdf:li>1</rdf:li><rdf:li>0.25</rdf:li></rdf:Seq>'
                    b'</hdrgm:GainMapMin>')
        self.assertEqual(fields(packet(b"", sequence * 600)), {"GainMapMin": ["0.5", "1", "0.25"]})
        self.assertEqual(fields(packet(b"", sequence.replace(b"<rdf:li>1</rdf:li>", b"<rdf:li>1</rdf:li>" * 2))), {})


if __name__ == "__main__":
    unittest.main()
