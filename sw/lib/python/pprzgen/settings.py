"""Port of sw/lib/ocaml/settings.ml"""

from dataclasses import dataclass, field, replace
from typing import List, Optional

from . import xml_light as X
from . import ext_xml
from .ocaml import Failure


def _is(xml, tagname):
    return isinstance(xml, X.Element) and xml.tag == tagname


@dataclass
class DlSetting:
    var: str
    shortname: Optional[str]
    handler: Optional[str]
    header: Optional[str]
    xml: X.Element


def dl_setting_from_xml(xml):
    if not _is(xml, "dl_setting"):
        raise Failure("Settings.Dl_setting.from_xml: unreachable")
    m = ext_xml.attrib_opt(xml, "module")
    h = ext_xml.attrib_opt(xml, "header")
    return DlSetting(var=X.attrib(xml, "var"),
                     shortname=ext_xml.attrib_opt(xml, "shortname"),
                     handler=ext_xml.attrib_opt(xml, "handler"),
                     header=m if m is not None else h,
                     xml=xml)


@dataclass
class DlSettings:
    name: Optional[str]
    dl_settings: list
    dl_setting: list
    headers: list
    xml: X.Element


def _iter_xml(s, xml):
    if _is(xml, "dl_settings"):
        return DlSettings(name=ext_xml.attrib_opt(xml, "name"),
                          dl_settings=[_iter_xml(s, c) for c in xml.children],
                          dl_setting=[], headers=[], xml=xml)
    if _is(xml, "dl_setting"):
        return replace(s, dl_setting=[dl_setting_from_xml(xml)] + s.dl_setting)
    if _is(xml, "include") and len(xml.attribs) == 1 and xml.attribs[0][0] == "header":
        return replace(s, headers=[xml.attribs[0][1]] + s.headers)
    raise Failure("Settings.Dl_settings.iter_xml: unreachable")


def dl_settings_from_xml(xml):
    return _iter_xml(DlSettings(None, [], [], [], X.Element("dl_settings")), xml)


@dataclass
class Settings:
    filename: str
    name: Optional[str]
    target: Optional[str]
    dl_settings: list
    xml: X.Element


def from_xml(xml):
    if not _is(xml, "settings"):
        raise Failure("Settings.from_xml: unreachable")
    return Settings(filename="",
                    name=ext_xml.attrib_opt(xml, "name"),
                    target=ext_xml.attrib_opt(xml, "target"),
                    dl_settings=[dl_settings_from_xml(c) for c in xml.children],
                    xml=xml)


def from_file(filename):
    return replace(from_xml(ext_xml.parse_file(filename)), filename=filename)


def get_headers(settings):
    def it(headers, s):
        for d in s.dl_setting:
            if d.header is not None and d.header not in headers:
                headers = [d.header] + headers
        for h in s.headers:
            if h not in headers:
                headers = [h] + headers
        for ss in s.dl_settings:
            headers = it(headers, ss)
        return headers
    headers = []
    for s in settings.dl_settings:
        headers = it(headers, s)
    return headers


def get_settings_xml(settings):
    lst = []
    for s in settings:
        if len(s.dl_settings) > 0:
            lst = X.children(ext_xml.child(s.xml, "dl_settings")) + lst
    lst = list(reversed(lst))
    return X.Element("settings", [], [X.Element("dl_settings", [], lst)])
