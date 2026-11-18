"""Port of sw/lib/ocaml/telemetry.ml"""

from dataclasses import dataclass, replace
from typing import Optional

from . import xml_light as X
from . import ext_xml
from . import settings as S
from .ocaml import Failure


def _is(xml, tagname):
    return isinstance(xml, X.Element) and xml.tag == tagname


@dataclass
class Message:
    name: str
    period: tuple  # ("period", f) | ("freq", f)
    phase: Optional[float]
    xml: X.Element


def message_from_xml(xml):
    if not (_is(xml, "message") and not xml.children):
        raise Failure("Telemetry.Message.from_xml: unreachable")
    name = X.attrib(xml, "name")
    p = ext_xml.attrib_opt_float(xml, "period")
    f = ext_xml.attrib_opt_float(xml, "freq")
    if p is not None and f is None:
        period = ("period", p)
    elif p is None and f is not None:
        period = ("freq", f)
    elif p is not None:
        raise Failure("Telemetry.Message.from_xml: either specify 'period' or 'freq' attribute, not both")
    else:
        raise Failure("Telemetry.Message.from_xml: specify 'period' or 'freq' attribute")
    return Message(name, period, ext_xml.attrib_opt_float(xml, "phase"), xml)


@dataclass
class Mode:
    name: str
    key_press: Optional[str]
    messages: list
    xml: X.Element


def mode_from_xml(xml):
    if not _is(xml, "mode"):
        raise Failure("Telemetry.Mode.from_xml: unreachable")
    return Mode(X.attrib(xml, "name"), ext_xml.attrib_opt(xml, "key_press"),
                [message_from_xml(m) for m in xml.children], xml)


@dataclass
class Process:
    name: str
    proc_type: Optional[str]
    modes: list
    xml: X.Element


def process_from_xml(xml):
    if not _is(xml, "process"):
        raise Failure("Telemetry.Process.from_xml: unreachable")
    return Process(X.attrib(xml, "name"), ext_xml.attrib_opt(xml, "type"),
                   [mode_from_xml(m) for m in xml.children], xml)


@dataclass
class Telemetry:
    filename: str
    processes: list
    xml: X.Element


def from_xml(xml):
    if not (_is(xml, "telemetry") and not xml.attribs):
        raise Failure("Telemetry.from_xml: unreachable")
    return Telemetry("", [process_from_xml(p) for p in xml.children], xml)


def from_file(filename):
    return replace(from_xml(ext_xml.parse_file(filename)), filename=filename)


def get_sys_telemetry_settings(telemetry):
    if telemetry is None:
        return None
    tl = []
    for p in telemetry.processes:
        if len(p.modes) > 1:
            modes = [m.name for m in p.modes]
            keys = []
            for i, m in enumerate(p.modes):
                if m.key_press is not None:
                    keys.insert(0, X.Element("key_press", [("key", m.key_press), ("value", str(i))], []))
            tl.append(X.Element("dl_setting",
                                [("min", "0"), ("step", "1"), ("max", str(len(modes) - 1)),
                                 ("var", "telemetry_mode_" + p.name), ("shortname", p.name),
                                 ("values", "|".join(modes)),
                                 ("header", "generated/periodic_telemetry")], keys))
    if tl:
        xml = X.Element("dl_settings", [("name", "Telemetry")], tl)
        return S.from_xml(X.Element("settings", [], [xml]))
    return None
