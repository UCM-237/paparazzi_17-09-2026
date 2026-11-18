"""Port of sw/lib/ocaml/module.ml: module XML description."""

import os
import re
import sys
from dataclasses import dataclass, field, replace
from typing import List, Optional

from .ocaml import concat as _concat
from . import xml_light as X
from . import ext_xml, env
from . import gen_common as GC
from . import settings as S
from .ocaml import Failure, NotFound, int_of_string, float_of_string


def _is(xml, tagname, no_children=False):
    return (isinstance(xml, X.Element) and xml.tag == tagname
            and (not no_children or not xml.children))


def find_name(xml):
    try:
        name = X.attrib(xml, "name")
    except X.NoAttribute:
        # the OCaml code catches Not_found, which Xml.attrib never raises
        raise
    if name.endswith(".xml"):
        return name[:-4]
    return name


@dataclass
class File:
    filename: str
    directory: Optional[str]
    filecond: Optional[str]


def parse_file(xml):
    if _is(xml, "file", True) or _is(xml, "file_arch", True):
        return File(find_name(xml), ext_xml.attrib_opt(xml, "dir"), ext_xml.attrib_opt(xml, "cond"))
    raise Failure("Module.parse_file: unreachable")


@dataclass
class Configure:
    cname: str
    cvalue: Optional[str]
    case: Optional[str]
    default: Optional[str]
    cdescription: Optional[str]


def parse_configure(xml):
    g = lambda a: ext_xml.attrib_opt(xml, a)
    return Configure(find_name(xml), g("value"), g("case"), g("default"), g("description"))


@dataclass
class Field:
    finame: Optional[str]
    fivalue: Optional[str]
    fitype: Optional[str]


@dataclass
class Define:
    dname: str
    dvalue: Optional[str]
    integer: Optional[int]
    dunit: Optional[str]
    dtype: Optional[str]
    ddescription: Optional[str]
    cond: Optional[str]
    fields: list


def parse_field(xml):
    return Field(ext_xml.attrib_opt(xml, "name"), ext_xml.attrib_opt(xml, "value"),
                 ext_xml.attrib_opt(xml, "type"))


def parse_define(xml):
    g = lambda a: ext_xml.attrib_opt(xml, a)
    integer = g("integer")
    return Define(find_name(xml), g("value"),
                  None if integer is None else int_of_string(integer),
                  g("unit"), g("type"), g("description"), g("cond"),
                  [parse_field(c) for c in X.children(xml)])


@dataclass
class Incl:
    element: str
    condition: Optional[str]


@dataclass
class Flag:
    flag: str
    value: str
    fcond: Optional[str]


@dataclass
class Makefile:
    targets: Optional[str] = None
    firmware: Optional[str] = None
    condition: Optional[str] = None
    configures: list = field(default_factory=list)
    defines: list = field(default_factory=list)
    inclusions: list = field(default_factory=list)
    flags: list = field(default_factory=list)
    files: list = field(default_factory=list)
    files_arch: list = field(default_factory=list)
    raws: list = field(default_factory=list)


def empty_makefile():
    return Makefile()


def parse_makefile(mkf, xml):
    """Lists are built by prepending (reverse document order), as in OCaml."""
    if _is(xml, "makefile"):
        mkf = replace(mkf, targets=ext_xml.attrib_opt(xml, "target"),
                      firmware=ext_xml.attrib_opt(xml, "firmware"),
                      condition=ext_xml.attrib_opt(xml, "cond"))
        for c in xml.children:
            mkf = parse_makefile(mkf, c)
        return mkf
    if _is(xml, "configure", True):
        return replace(mkf, configures=[parse_configure(xml)] + mkf.configures)
    if _is(xml, "define", True):
        return replace(mkf, defines=[parse_define(xml)] + mkf.defines)
    if _is(xml, "include", True):
        return replace(mkf, inclusions=[Incl(find_name(xml), ext_xml.attrib_opt(xml, "cond"))] + mkf.inclusions)
    if _is(xml, "flag", True):
        fl = X.attrib(xml, "name")
        val = X.attrib(xml, "value")
        return replace(mkf, flags=[Flag(fl, val, ext_xml.attrib_opt(xml, "cond"))] + mkf.flags)
    if _is(xml, "file", True):
        return replace(mkf, files=[parse_file(xml)] + mkf.files)
    if _is(xml, "file_arch", True):
        return replace(mkf, files_arch=[parse_file(xml)] + mkf.files_arch)
    if (_is(xml, "raw") and not xml.attribs and len(xml.children) == 1
            and isinstance(xml.children[0], str)):
        return replace(mkf, raws=[xml.children[0]] + mkf.raws)
    if _is(xml, "test"):
        return mkf
    raise Failure("Module.parse_makefile: unreachable")


# autorun: "true" | "false" | "lock"
# period_freq: ("unset",) | ("set", period, freq) | ("freq", str) | ("period", str)


@dataclass
class Periodic:
    call: str
    fname: str
    period_freq: tuple
    delay: Optional[float]
    start: Optional[str]
    stop: Optional[str]
    autorun: str
    cond: Optional[str]


_CALL_RE = re.compile(r"([a-zA-Z_][a-zA-Z0-9_]*)(.*)", re.S)


def parse_periodic(xml):
    g = lambda a: ext_xml.attrib_opt(xml, a)
    call = None
    for a, v in X.attribs(xml):
        if a.lower() == "fun":
            call = v
            break
    if call is None:
        raise NotFound("fun")
    # Str '.' does not match newline
    m = re.match(r"([a-zA-Z_][a-zA-Z0-9_]*)([^\n]*)", call)
    if m:
        fname, args = m.group(1), m.group(2)
        if not (args == "" or re.match(r"\([^\n]*\)", args)):
            raise Failure("Module.parse_periodic: invalid function call: " + call)
    else:
        raise Failure("Module.parse_periodic: invalid function name: " + call)
    p, f = g("period"), g("freq")

    def as_set_p(p):
        try:
            pv = float_of_string(p)
            return ("set", pv, _div(1.0, pv))
        except Failure:
            return ("period", p)

    if p is None and f is None:
        pf = ("unset",)
    elif p is None:
        try:
            fv = float_of_string(f)
            pf = ("set", _div(1.0, fv), fv)
        except Failure:
            pf = ("freq", f)
    elif f is None:
        pf = as_set_p(p)
    else:
        sys.stderr.write("Warning: both period and freq are defined ")
        sys.stderr.write(f"but only period is used for function {fname}\n")
        sys.stderr.flush()
        pf = as_set_p(p)
    autorun = g("autorun")
    if autorun is None:
        ar = "lock"
    elif autorun in ("TRUE", "true"):
        ar = "true"
    elif autorun in ("FALSE", "false"):
        ar = "false"
    elif autorun in ("LOCK", "lock"):
        ar = "lock"
    else:
        raise Failure("Module.parse_periodic: unknown autorun: " + autorun)
    return Periodic(call, fname, pf, ext_xml.attrib_opt_float(xml, "delay"),
                    g("start"), g("stop"), ar, g("cond"))


def _div(a, b):
    """OCaml/IEEE float division (x/0 = +-inf or nan, no exception)."""
    try:
        return a / b
    except ZeroDivisionError:
        import math
        if a == 0 or math.isnan(a):
            return math.nan
        return math.copysign(math.inf, a) * math.copysign(1.0, b)


@dataclass
class Init:
    iname: str
    cond: Optional[str]


@dataclass
class Event:
    ev: str
    cond: Optional[str]


@dataclass
class Datalink:
    message: str
    func: str
    dl_class: Optional[str]
    cond: Optional[str]


@dataclass
class Dependencies:
    requires: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)
    provides: list = field(default_factory=list)
    recommends: list = field(default_factory=list)
    suggests: list = field(default_factory=list)


def _split(regex, s):
    """Str.split: split on regex, dropping empty leading/trailing fields."""
    parts = re.split(regex, s)
    while parts and parts[0] == "":
        parts.pop(0)
    while parts and parts[-1] == "":
        parts.pop()
    return parts


def parse_comma_list(s):
    return _split(r"[ \t]*,[ \t]*", s)


def parse_func_list(s):
    return ["@" + x for x in _split(r"[ \t]*,[ \t]*", s)]


def parse_module_options(s):
    return _split(r"[ \t]*\|[ \t]*", s)


def parse_dependencies(dep, xml):
    def single_text(tagname):
        return (_is(xml, tagname) and len(xml.children) == 1 and isinstance(xml.children[0], str))
    if _is(xml, "dep"):
        for c in xml.children:
            dep = parse_dependencies(dep, c)
        return dep
    if single_text("depends"):
        return replace(dep, requires=[GC.bool_expr_of_string(x) for x in parse_comma_list(xml.children[0])])
    if single_text("conflicts"):
        return replace(dep, conflicts=parse_comma_list(xml.children[0]))
    if single_text("provides"):
        return replace(dep, provides=parse_func_list(xml.children[0]))
    if single_text("recommends"):
        return replace(dep, recommends=[GC.bool_expr_of_string(x) for x in parse_comma_list(xml.children[0])])
    if single_text("suggests"):
        return replace(dep, suggests=parse_comma_list(xml.children[0]))
    raise Failure("Module.parse_dependencies: unreachable")


@dataclass
class Config:
    name: str
    mtype: Optional[str]
    dir: Optional[str]
    configures: list
    defines: list
    xml: X.Element


def config_from_xml(xml):
    if not _is(xml, "module"):
        raise Failure("Module.config_from_xml: unreachable")
    return Config(X.attrib(xml, "name"), ext_xml.attrib_opt(xml, "type"),
                  ext_xml.attrib_opt(xml, "dir"),
                  ext_xml.parse_children("configure", parse_configure, xml.children),
                  ext_xml.parse_children("define", parse_define, xml.children), xml)


@dataclass
class Module:
    xml_filename: str = ""
    name: str = ""
    dir: Optional[str] = None
    task: Optional[str] = None
    path: str = ""
    doc: X.Element = field(default_factory=lambda: X.Element("doc"))
    dependencies: Optional[Dependencies] = None
    settings: list = field(default_factory=list)
    headers: list = field(default_factory=list)
    inits: list = field(default_factory=list)
    periodics: list = field(default_factory=list)
    events: list = field(default_factory=list)
    datalinks: list = field(default_factory=list)
    makefiles: list = field(default_factory=list)
    xml: X.Element = field(default_factory=lambda: X.Element("module"))


def empty():
    return Module()


def parse_xml(m, xml):
    if _is(xml, "module"):
        m = replace(m, name=find_name(xml), dir=ext_xml.attrib_opt(xml, "dir"),
                    task=ext_xml.attrib_opt(xml, "task"), xml=xml)
        for c in xml.children:
            m = parse_xml(m, c)
        return m
    if _is(xml, "doc"):
        return replace(m, doc=xml)
    if _is(xml, "settings"):
        return replace(m, settings=[S.from_xml(xml)] + m.settings)
    if _is(xml, "dep"):
        return replace(m, dependencies=parse_dependencies(Dependencies(), xml))
    if _is(xml, "header") and not xml.attribs:
        headers = m.headers
        for f in xml.children:
            headers = [parse_file(f)] + headers
        return replace(m, headers=headers)
    if _is(xml, "init", True):
        f = X.attrib(xml, "fun")
        return replace(m, inits=[Init(f, ext_xml.attrib_opt(xml, "cond"))] + m.inits)
    if _is(xml, "periodic", True):
        return replace(m, periodics=[parse_periodic(xml)] + m.periodics)
    if _is(xml, "event", True):
        f = X.attrib(xml, "fun")
        return replace(m, events=[Event(f, ext_xml.attrib_opt(xml, "cond"))] + m.events)
    if _is(xml, "datalink", True):
        message = X.attrib(xml, "message")
        func = X.attrib(xml, "fun")
        return replace(m, datalinks=[Datalink(message, func, ext_xml.attrib_opt(xml, "class"),
                                              ext_xml.attrib_opt(xml, "cond"))] + m.datalinks)
    if _is(xml, "makefile"):
        return replace(m, makefiles=[parse_makefile(empty_makefile(), xml)] + m.makefiles)
    raise Failure("Module.parse_xml: unreachable")


def from_xml(xml):
    m = parse_xml(empty(), xml)
    return replace(m, settings=list(reversed(m.settings)), headers=list(reversed(m.headers)),
                   inits=list(reversed(m.inits)), makefiles=list(reversed(m.makefiles)))


def from_file(filename):
    return from_xml(ext_xml.parse_file(filename))


class ModuleNotFound(Exception):
    """Module.Module_not_found (uncaught in the generators: exit code 2)."""


def _is_implicit(name):
    """Filename.is_implicit (Unix)."""
    return not (name.startswith("/") or name.startswith("./") or name.startswith("../"))


def from_module_name(mtype, name):
    if mtype is not None:
        name = name + "_" + mtype
    if not name.endswith(".xml"):
        name = name + ".xml"
    if _is_implicit(name):
        for b in env.modules_paths:
            full = _concat(b, name)
            if os.path.exists(full):
                name = full
                break
        else:
            raise ModuleNotFound(name)
    elif not os.path.exists(name):
        raise ModuleNotFound(name)
    m = from_xml(ext_xml.parse_file(name))
    settings = [replace(s, filename=name) for s in m.settings]
    return replace(m, xml_filename=name, settings=settings)


def check_mk(target, firmware, mk):
    return ((mk.firmware == firmware or mk.firmware is None or firmware == "none")
            and GC.test_targets(target, GC.bool_expr_of_string(mk.targets)))


def check_loading(target, firmware, m):
    return any(check_mk(target, firmware, mk) for mk in m.makefiles)


def status_name(mod_name, p):
    return mod_name + "_" + p.fname + "_status"


def get_sys_modules_settings(modules):
    mod_settings = []
    for m in modules:
        for p in m.periodics:
            if p.autorun != "lock":
                mod_settings.append(X.Element("dl_setting",
                                              [("min", "2"), ("max", "3"), ("step", "1"),
                                               ("var", status_name(m.name, p)),
                                               ("shortname", p.fname), ("values", "START|STOP")], []))
    xml = X.Element("dl_settings", [("name", "Modules")], mod_settings)
    if mod_settings:
        return S.from_xml(X.Element("settings", [], [xml]))
    return None
