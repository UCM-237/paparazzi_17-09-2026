"""Port of sw/lib/ocaml/autopilot.ml (model only, not the generator)."""

from dataclasses import dataclass, replace

from . import xml_light as X
from . import ext_xml
from . import module as M
from . import settings as S
from .ocaml import Failure


@dataclass
class Autopilot:
    filename: str
    modules: list
    xml: X.Element


def from_xml(xml):
    if not (isinstance(xml, X.Element) and xml.tag == "autopilot"):
        raise Failure("Autopilot.from_xml: unreachable")
    grand = []
    for c in xml.children:
        grand += X.children(c)
    modules = []
    for el in grand:
        if X.tag(el) == "modules":
            modules = modules + [M.config_from_xml(c) for c in X.children(el)]
    return Autopilot("", modules, xml)


def from_file(filename):
    return replace(from_xml(ext_xml.parse_file(filename)), filename=filename)


def get_sys_ap_settings(autopilots):
    if autopilots is None:
        return None
    sl = []
    for _freq, autopilot in autopilots:
        settings = []
        for sm in X.children(autopilot.xml):
            try:
                xml_settings = ext_xml.child(sm, "settings")
                settings = settings + X.children(xml_settings)
            except Exception:
                pass

        def settings_mode(sm):
            try:
                return X.attrib(sm, "settings_mode").lower() == "true"
            except Exception:
                return False

        sm_filtered = [sm for sm in X.children(autopilot.xml) if settings_mode(sm)]
        dl_set = []
        for sm in sm_filtered:
            modes = [m for m in X.children(sm) if X.tag(m) == "mode"]
            name = X.attrib(sm, "name")
            current, mn, mx, values = 0, None, None, []
            for m in modes:
                try:
                    pr = X.attrib(m, "settings").lower() != "hide"
                except Exception:
                    pr = True
                mname = X.attrib(m, "name")
                if pr:
                    if mn is None:
                        mn = current
                    mx = current
                    values = values + [mname]
                else:
                    values = values + ([] if mn is None else [mname])
                current += 1
            try:
                sh = X.attrib(sm, "settings_handler")
                s = [x for x in sh.split("|") if x != ""]
                if len(s) != 2:
                    raise Failure("Gen_autopilot: invalid handler format")
                handler = [("header", s[0]), ("handler", s[1])]
            except Exception:
                handler = [("header", "autopilot_core_" + name)]
            if mn is not None and mx is not None:
                dl_set.insert(0, X.Element("dl_setting",
                                           [("min", str(mn)), ("max", str(mx)), ("step", "1"),
                                            ("var", "autopilot_mode_" + name), ("shortname", name),
                                            ("values", "|".join(values))] + handler, []))
        sl = dl_set + settings + sl
    xml = X.Element("dl_settings", [("name", "Autopilot")], sl)
    return S.from_xml(X.Element("settings", [], [xml]))
