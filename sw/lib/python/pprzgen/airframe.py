"""Port of sw/lib/ocaml/airframe.ml (model only, not the generator)."""

import os
import re
import sys
from dataclasses import dataclass, replace
from typing import Optional

from .ocaml import concat as _concat
from . import xml_light as X
from . import ext_xml, env
from . import module as M
from .ocaml import Failure, NotFound


def _is(xml, tagname):
    return isinstance(xml, X.Element) and xml.tag == tagname


@dataclass
class AfAutopilot:
    name: str
    freq: Optional[str]
    xml: X.Element


def autopilot_from_xml(xml):
    if not (_is(xml, "autopilot") and not xml.children):
        raise Failure("Airframe.Autopilot.from_xml: unreachable")
    return AfAutopilot(X.attrib(xml, "name"), ext_xml.attrib_opt(xml, "freq"), xml)


@dataclass
class Include:
    href: str
    xml: X.Element


def include_from_xml(xml):
    if not (_is(xml, "include") and not xml.children):
        raise Failure("Airframe.Include.from_xml: unreachable")
    return Include(X.attrib(xml, "href"), xml)


def _opt_autopilot(xml):
    try:
        return autopilot_from_xml(ext_xml.child(xml, "autopilot"))
    except Exception:
        return None


@dataclass
class Target:
    name: str
    board: str
    modules: list
    autopilot: Optional[AfAutopilot]
    configures: list
    defines: list
    xml: X.Element


def target_from_xml(xml):
    if not _is(xml, "target"):
        raise Failure("Airframe.Autopilot.from_xml: unreachable")
    ch = xml.children
    return Target(X.attrib(xml, "name"), X.attrib(xml, "board"),
                  ext_xml.parse_children("module", M.config_from_xml, ch),
                  _opt_autopilot(xml),
                  ext_xml.parse_children("configure", M.parse_configure, ch),
                  ext_xml.parse_children("define", M.parse_define, ch), xml)


@dataclass
class Firmware:
    name: str
    targets: list
    modules: list
    autopilot: Optional[AfAutopilot]
    configures: list
    defines: list
    xml: X.Element


def firmware_from_xml(xml):
    if not _is(xml, "firmware"):
        raise Failure("Airframe.Firmware.from_xml: unreachable")
    ch = xml.children
    return Firmware(X.attrib(xml, "name"),
                    ext_xml.parse_children("target", target_from_xml, ch),
                    ext_xml.parse_children("module", M.config_from_xml, ch),
                    _opt_autopilot(xml),
                    ext_xml.parse_children("configure", M.parse_configure, ch),
                    ext_xml.parse_children("define", M.parse_define, ch), xml)


@dataclass
class OldModules:
    modules: list
    xml: X.Element


def old_modules_from_xml(xml):
    if not _is(xml, "modules"):
        raise Failure("Airframe.Modules.from_xml: unreachable")
    return OldModules(ext_xml.parse_children("module", M.config_from_xml, xml.children), xml)


@dataclass
class Airframe:
    filename: str
    name: str
    includes: list
    modules: list
    firmwares: list
    autopilots: list
    xml: X.Element


def from_xml(xml):
    if not _is(xml, "airframe"):
        raise Failure("Airframe.from_xml: unreachable")
    ch = xml.children
    if any(X.tag(c) == "modules" for c in ch):
        sys.stderr.write("\nWarning: 'modules' node is deprecated, please move modules to 'firmware' section\n")
        sys.stderr.flush()
    return Airframe("", X.attrib(xml, "name"),
                    ext_xml.parse_children("include", include_from_xml, ch),
                    ext_xml.parse_children("modules", old_modules_from_xml, ch),
                    ext_xml.parse_children("firmware", firmware_from_xml, ch),
                    ext_xml.parse_children("autopilot", autopilot_from_xml, ch), xml)


def from_file(filename):
    return replace(from_xml(ext_xml.parse_file(filename)), filename=filename)


def expand_includes(ac_id, xml):
    if not isinstance(xml, X.Element):
        return xml
    kids = []
    for c in xml.children:
        if X.tag(c) == "include":
            filename = ext_xml.attrib(c, "href").replace("$AC_ID", ac_id)
            if not filename.startswith("/"):
                filename = _concat(env.paparazzi_home, filename)
            kids += X.children(ext_xml.parse_file(filename))
        else:
            kids.append(c)
    return X.Element(xml.tag, xml.attribs, kids)
