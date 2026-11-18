"""Port of sw/lib/ocaml/radio.ml"""

from dataclasses import dataclass, replace

from . import xml_light as X
from . import ext_xml
from .ocaml import Failure


@dataclass
class Channel:
    cname: str
    min: int
    max: int
    neutral: int
    average: bool
    reverse: bool


def parse_channel(xml):
    if not (isinstance(xml, X.Element) and xml.tag == "channel" and not xml.children):
        raise Failure("Radio.parse_channel: unreachable")

    def bget(a):
        try:
            return X.attrib(xml, a) != "0"
        except X.NoAttribute:
            return False

    reverse = bget("reverse")
    mn = ext_xml.int_attrib(xml, "min")
    mx = ext_xml.int_attrib(xml, "max")
    return Channel(cname=X.attrib(xml, "function"),
                   min=mx if reverse else mn,
                   max=mn if reverse else mx,
                   neutral=ext_xml.int_attrib(xml, "neutral"),
                   average=bget("average"), reverse=reverse)


@dataclass
class Radio:
    filename: str
    name: str
    data_min: int
    data_max: int
    sync_min: int
    sync_max: int
    pulse_type: str
    channels: list
    xml: X.Element


def from_xml(xml):
    if not (isinstance(xml, X.Element) and xml.tag == "radio"):
        raise Failure("Radio.from_xml: unreachable")
    name = X.attrib(xml, "name")
    data_min = ext_xml.int_attrib(xml, "data_min")
    data_max = ext_xml.int_attrib(xml, "data_max")
    sync_min = ext_xml.int_attrib(xml, "sync_min")
    sync_max = ext_xml.int_attrib(xml, "sync_max")
    pt = X.attrib(xml, "pulse_type")
    if pt not in ("POSITIVE", "NEGATIVE"):
        raise Failure("Radio.from_xml: unknown pulse type")
    return Radio("", name, data_min, data_max, sync_min, sync_max, pt,
                 [parse_channel(c) for c in xml.children], xml)


def from_file(filename):
    return replace(from_xml(ext_xml.parse_file(filename)), filename=filename)
