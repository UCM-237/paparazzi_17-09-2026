"""The parts of the OCaml PprzLink library used by the generators."""

import os

from .ocaml import concat as _concat
from . import xml_light as X
from .ocaml import Failure, NotFound, float_of_string


def _pprzlink_dir():
    d = os.environ.get("PPRZLINK_DIR")
    if d is not None:
        return d
    h = os.environ.get("PAPARAZZI_HOME")
    if h is not None:
        return _concat(h, "var")
    return "/usr/share/pprzlink"


pprzlink_dir = _pprzlink_dir()
messages_file = _concat(pprzlink_dir, "messages.xml")
units_file = _concat(pprzlink_dir, "units.xml")

_messages = None


def messages_xml():
    global _messages
    if _messages is None:
        _messages = X.parse_file(messages_file)
    return _messages


class UnitConversionError(Exception):
    pass


class UnknownConversion(Exception):
    def __init__(self, su, scu):
        super().__init__(su, scu)
        self.su, self.scu = su, scu


class NoAutomaticConversion(Exception):
    pass


def scale_of_units(from_unit, to_unit, auto=None):
    if from_unit == to_unit:
        return 1.0
    try:
        units_xml = X.parse_file(units_file)
        found = None
        for u in X.children(units_xml):
            f = X.attrib(u, "from")
            t = X.attrib(u, "to")
            try:
                a = X.attrib(u, "auto")
            except Exception:
                a = None
            if a is None:
                a = ""
            elif auto is not None:
                a = auto.lower()
            else:
                a = a.lower()
            if (f == from_unit or a == "display") and (t == to_unit or a == "code"):
                found = u
                break
        if found is None:
            raise NotFound()
        return float_of_string(X.attrib(found, "coef"))
    except (X.NoAttribute, X.NotElement):
        raise UnitConversionError("File conf/units.xml has errors")
    except Failure:
        raise UnitConversionError("Unit coef is not numerical value")
    except NotFound:
        if from_unit == "" or to_unit == "":
            raise NoAutomaticConversion(from_unit, to_unit)
        raise UnknownConversion(from_unit, to_unit)
    except Exception:
        # includes xml-light's File_not_found (OCaml catches the wrong one)
        raise UnknownConversion(from_unit, to_unit)
