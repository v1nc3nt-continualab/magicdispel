"""Keep only the XMP fields that describe how an HDR gain map is rendered.

`hdr_fields` reads a packet and returns the recognized fields; `hdr_packet`
writes a new packet holding just those, so no other XMP can carry over.
"""
import math
import xml.etree.ElementTree as ET

RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
ADOBE_GAIN_MAP = "http://ns.adobe.com/hdr-gain-map/1.0/"
APPLE_PIXEL_DATA = "http://ns.apple.com/pixeldatainfo/1.0/"
APPLE_GAIN_MAP = "http://ns.apple.com/HDRGainMap/1.0/"
PREFIXES = {"x": "adobe:ns:meta/", "rdf": RDF, "hdrgm": ADOBE_GAIN_MAP,
            "apdi": APPLE_PIXEL_DATA, "HDRGainMap": APPLE_GAIN_MAP}
# Numbers (one value, or one per RGB channel) that decoders use to render HDR.
NUMERIC = {
    ADOBE_GAIN_MAP: {"Version", "GainMapMin", "GainMapMax", "Gamma", "OffsetSDR", "OffsetHDR",
                     "HDRCapacityMin", "HDRCapacityMax", "BaseHeadroom", "AlternateHeadroom"},
    APPLE_PIXEL_DATA: {"NativeFormat", "StoredFormat"},
    APPLE_GAIN_MAP: {"HDRGainMapVersion", "HDRGainMapHeadroom"},
}
CHOICES = {
    (ADOBE_GAIN_MAP, "BaseRenditionIsHDR"): {"True", "False", "0", "1"},
    (APPLE_PIXEL_DATA, "AuxiliaryImageType"): {"urn:com:apple:photo:2020:aux:hdrgainmap"},
}


class XMPError(ValueError):
    pass


def hdr_fields(packet):
    """[(namespace, name, value or [values])] for the recognized HDR fields."""
    packet = packet.rstrip(b"\0 \t\r\n")
    if not packet:
        return []
    if b"<!DOCTYPE" in packet or b"<!ENTITY" in packet:
        raise XMPError("XMP with a document type or entities")
    try:
        root = ET.fromstring(packet)
    except ET.ParseError as error:
        raise XMPError(str(error)) from error
    fields = []
    for description in root.iter("{%s}Description" % RDF):
        for name, value in description.attrib.items():
            if permitted(name, value):
                fields.append((*split(name), value))
        for child in description:
            if len(child) == 0 and permitted(child.tag, child.text or ""):
                fields.append((*split(child.tag), child.text or ""))
            elif len(child) == 1 and child[0].tag in {"{%s}Seq" % RDF, "{%s}Bag" % RDF}:
                items = list(child[0])
                values = [item.text or "" for item in items]
                if all(item.tag == "{%s}li" % RDF and len(item) == 0 for item in items) \
                        and permitted(child.tag, values):
                    fields.append((*split(child.tag), values))
    return fields


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
    return ET.tostring(root, encoding="utf-8")


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
    values = value if isinstance(value, list) else [value]
    try:
        return len(values) in (1, 3) and all(math.isfinite(float(v)) and abs(float(v)) <= 1e12
                                             for v in values)
    except (TypeError, ValueError, OverflowError):
        return False
