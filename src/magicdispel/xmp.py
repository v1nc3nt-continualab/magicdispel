"""Keep only the XMP fields that describe how an HDR gain map is rendered.

`hdr_fields` reads a packet and returns the recognized fields, each once and
its numbers written the one way; `hdr_packet` writes a new packet holding just
those, so no other XMP can carry over.
"""
import re
import xml.etree.ElementTree as ET
from decimal import Decimal

RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
XMP_NOTE = "http://ns.adobe.com/xmp/note/"  # where a packet names its extension
ADOBE_GAIN_MAP = "http://ns.adobe.com/hdr-gain-map/1.0/"
APPLE_PIXEL_DATA = "http://ns.apple.com/pixeldatainfo/1.0/"
APPLE_GAIN_MAP = "http://ns.apple.com/HDRGainMap/1.0/"
PREFIXES = {"x": "adobe:ns:meta/", "rdf": RDF, "hdrgm": ADOBE_GAIN_MAP,
            "apdi": APPLE_PIXEL_DATA, "HDRGainMap": APPLE_GAIN_MAP}
# Numbers (one value, or one per RGB channel) that decoders use to render HDR.
NUMERIC = {
    ADOBE_GAIN_MAP: {"Version", "GainMapMin", "GainMapMax", "Gamma", "OffsetSDR", "OffsetHDR",
                     "HDRCapacityMin", "HDRCapacityMax", "BaseHeadroom", "AlternateHeadroom"},
    # Stored and native pixel formats of Apple's gain map, and its value range.
    APPLE_PIXEL_DATA: {"NativeFormat", "StoredFormat", "IntMinValue", "IntMaxValue",
                       "FloatMinValue", "FloatMaxValue"},
    APPLE_GAIN_MAP: {"HDRGainMapVersion", "HDRGainMapHeadroom"},
}
CHOICES = {
    (ADOBE_GAIN_MAP, "BaseRenditionIsHDR"): {"True", "False", "0", "1"},
    (APPLE_PIXEL_DATA, "AuxiliaryImageType"): {"urn:com:apple:photo:2020:aux:hdrgainmap"},
}


# A number as XMP writes it, in ASCII: float() also takes spaces, underscores and the digits of other scripts.
NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", re.ASCII)
SMALLEST, LARGEST = 1e-12, 1e12  # of a number that is not zero: what a rendering value can be, and a bound on its text


class XMPError(ValueError):
    pass


def hdr_fields(packet):
    """[(namespace, name, value or [values])] for the recognized HDR fields.
    A field found twice counts once, the first time, and a number is written
    in its shortest form: whole numbers as they are, others as a float. What a
    number is written as could otherwise carry any digits along."""
    fields, seen = [], set()

    def add(name, value):
        if split(name) not in seen:
            seen.add(split(name))
            fields.append((*split(name), written(name, value)))

    for description in descriptions(packet):
        for name, value in description.attrib.items():
            if permitted(name, value):
                add(name, value)
        for child in description:
            if len(child) == 0 and permitted(child.tag, child.text or ""):
                add(child.tag, child.text or "")
            elif len(child) == 1 and child[0].tag in {"{%s}Seq" % RDF, "{%s}Bag" % RDF}:
                items = list(child[0])
                values = [item.text or "" for item in items]
                if all(item.tag == "{%s}li" % RDF and len(item) == 0 for item in items) \
                        and permitted(child.tag, values):
                    add(child.tag, values)
    return fields


def written(name, value):
    """A recognized field's value as it is written: a choice as it is, a number in its shortest form, without
    an exponent, which not every reader takes."""
    if split(name) in CHOICES:
        return value
    if isinstance(value, list):
        return [written(name, item) for item in value]
    text = value.strip(" \t\r\n")
    if re.fullmatch(r"[+-]?\d+", text, re.ASCII):
        return str(int(text))
    shortest = repr(float(text) + 0.0)  # + 0.0: minus zero is zero
    return format(Decimal(shortest), "f") if "e" in shortest else shortest


def extension(packet):
    """The GUID of the extended packet a main packet names (XMP part 3), as
    bytes, or None. Large XMP continues there, and Ultra HDR photos may keep
    their gain-map fields in it."""
    for description in descriptions(packet):
        value = description.get("{%s}HasExtendedXMP" % XMP_NOTE)
        child = description.find("{%s}HasExtendedXMP" % XMP_NOTE)
        value = value if value is not None else child.text if child is not None else None
        if value and len(value) == 32 and all(c in "0123456789ABCDEFabcdef" for c in value):
            return value.encode("ascii")
    return None


def descriptions(packet):
    """The rdf:Description elements of a packet."""
    packet = packet.rstrip(b"\0 \t\r\n")
    if not packet:
        return []
    if b"<!DOCTYPE" in packet or b"<!ENTITY" in packet:
        raise XMPError("XMP with a document type or entities")
    try:
        root = ET.fromstring(packet)
    except (ET.ParseError, LookupError, ValueError) as error:  # the latter two: an encoding it does not know
        raise XMPError(str(error)) from error
    return list(root.iter("{%s}Description" % RDF))


def hdr_packet(fields):
    """A new XMP packet with only `fields`, or b"" when there are none."""
    if not fields:
        return b""
    for prefix, uri in PREFIXES.items():
        ET.register_namespace(prefix, uri)
    root = ET.Element("{adobe:ns:meta/}xmpmeta")
    description = ET.SubElement(ET.SubElement(root, "{%s}RDF" % RDF), "{%s}Description" % RDF)
    for namespace, name, value in fields:
        tag = "{%s}%s" % (namespace, name)
        if isinstance(value, list):
            sequence = ET.SubElement(ET.SubElement(description, tag), "{%s}Seq" % RDF)
            for item in value:
                ET.SubElement(sequence, "{%s}li" % RDF).text = item
        else:
            description.set(tag, value)
    return ET.tostring(root, encoding="utf-8", xml_declaration=False)


def split(name):
    namespace, _, local = name[1:].partition("}")
    return namespace, local


def permitted(name, value):
    if not name.startswith("{"):
        return False
    namespace, local = split(name)
    if (namespace, local) in CHOICES:
        return isinstance(value, str) and value in CHOICES[namespace, local]
    return local in NUMERIC.get(namespace, ()) and numeric(value)


def numeric(value):
    """Whether `value`, a number or a list of three, is plain and of modest size: ExifTool's -n gives numbers."""
    values = value if isinstance(value, list) else [value]
    return len(values) in (1, 3) and all(plain_number(v) for v in values)


def plain_number(value):
    if isinstance(value, str):
        if len(value) > 40 or not NUMBER.fullmatch(value.strip(" \t\r\n")):
            return False
        value = float(value)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return value == 0 or SMALLEST <= abs(value) <= LARGEST  # false for NaN and infinity
