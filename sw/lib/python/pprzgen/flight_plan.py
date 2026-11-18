"""Port of sw/lib/ocaml/flight_plan.ml (model only, not the generator)."""

from dataclasses import dataclass, replace

from . import xml_light as X
from . import ext_xml
from . import module as M
from . import settings as S
from .ocaml import Failure


@dataclass
class FlightPlan:
    filename: str
    settings: list
    modules: list
    xml: X.Element


def from_xml(xml):
    if not (isinstance(xml, X.Element) and xml.tag == "flight_plan"):
        raise Failure("Flight_plan.from_xml: unreachable")
    settings = []
    for el in xml.children:
        if X.tag(el) == "variables":
            acc = []
            for e in X.children(el):
                if X.tag(e) == "variable":
                    has = lambda a: ext_xml.attrib_opt(e, a) is not None
                    if has("min") and has("max") and has("step"):
                        acc = [S.DlSetting(var=X.attrib(e, "var"),
                                           shortname=ext_xml.attrib_opt(e, "shortname"),
                                           handler=None, header=None,
                                           xml=X.Element("dl_setting", X.attribs(e), []))] + acc
            settings = settings + acc
    modules = []
    for el in xml.children:
        if X.tag(el) == "modules":
            modules = modules + [M.config_from_xml(c) for c in X.children(el)]
    return FlightPlan("", settings, modules, xml)


def from_file(filename):
    return replace(from_xml(ext_xml.parse_file(filename)), filename=filename)


def get_sys_fp_settings(flight_plan):
    if flight_plan is None:
        return None
    dl = flight_plan.settings
    if not dl:
        return None
    dl_xml = X.Element("dl_settings", [("name", "Flight Plan")], [x.xml for x in dl])
    dl_settings = S.DlSettings(name="Flight Plan", dl_settings=[], dl_setting=dl,
                               headers=["generated/flight_plan"], xml=dl_xml)
    xml = X.Element("settings", [], [X.Element("dl_settings", [], [dl_xml])])
    return S.Settings(filename="", name=None, target=None, dl_settings=[dl_settings], xml=xml)
