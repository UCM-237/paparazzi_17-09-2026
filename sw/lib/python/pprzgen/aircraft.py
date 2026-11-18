"""Port of sw/lib/ocaml/aircraft.ml: global configuration of an aircraft
(modules per target and dependency resolution)."""

import os
import re
import sys
from dataclasses import dataclass, field, replace
from typing import Optional

from .ocaml import concat as _concat
from . import xml_light as X
from . import ext_xml, env
from . import gen_common as GC
from . import module as M
from . import airframe as Af
from . import autopilot as Ap
from . import flight_plan as Fp
from . import radio as Rd
from . import telemetry as Tl
from . import settings as S
from .ocaml import Failure, NotFound, Hashtbl

# load types
USER_LOAD, UNLOADED, DEPEND, SUGGESTED = "U", "N", "D", "S"


def _out(s):
    sys.stdout.write(s)
    sys.stdout.flush()


@dataclass
class TargetConf:
    configures: list
    configures_default: list
    defines: list
    firmware_name: str
    board_type: str
    modules: list  # list of (load_type, Module)
    autopilot: bool


def init_target_conf(firmware_name, board_type):
    return TargetConf([], [], [], firmware_name, board_type, [], False)


@dataclass
class Aircraft:
    name: str
    config_by_target: dict
    all_modules: list
    airframe: object = None
    autopilots: object = None
    flight_plan: object = None
    radio: object = None
    telemetry: object = None
    settings: object = None
    xml: list = field(default_factory=list)


def target_conf_add_module_config(conf, target, firmware, m, load_type):
    configures = list(conf.configures)
    for mk in m.makefiles:
        if M.check_mk(target, firmware, mk):
            configures += [c for c in mk.configures if c.cvalue is not None]
    configures_default = list(conf.configures_default)
    for mk in m.makefiles:
        if M.check_mk(target, firmware, mk):
            configures_default += [c for c in mk.configures if not (c.default is None and c.case is None)]
    return replace(conf, configures=configures, configures_default=configures_default,
                   modules=conf.modules + [(load_type, m)])


def target_conf_add_module(conf, target, firmware, name, mtype, load_type):
    m = M.from_module_name(mtype, name)
    if M.check_loading(target, firmware, m):
        if any(m.name == lm.name for _, lm in conf.modules):
            add = []
        else:
            add = [(load_type, m)]
        return replace(conf, modules=conf.modules + add)
    return replace(conf, modules=conf.modules + [(UNLOADED, m)])


@dataclass
class SortResult:
    resolved: list = field(default_factory=list)    # (name, (load_type, Module))
    unresolved: list = field(default_factory=list)  # (name, Module)
    unloaded: list = field(default_factory=list)    # (load_type, Module)
    conflicts: list = field(default_factory=list)   # (conflict, module name)
    required: list = field(default_factory=list)    # (bool_expr, module name)
    provided: list = field(default_factory=list)    # (func, module name)
    suggested: list = field(default_factory=list)   # shared multi-map (func, Module)


def _assoc_mem(name, lst):
    return any(n == name for n, _ in lst)


def _assoc(name, lst):
    for n, v in lst:
        if n == name:
            return v
    raise NotFound(name)


def _remove_assoc(name, lst):
    out = list(lst)
    for i, (n, _) in enumerate(out):
        if n == name:
            del out[i]
            break
    return out


def _add_suggested(sol, dep):
    for s in dep.suggests:
        sm = M.from_module_name(None, s)
        if sm.dependencies is not None:
            for p in sm.dependencies.provides:
                sol.suggested.append((p, sm))


def _dep_resolve(sol, m, target, firmware, load_type):
    def test_module(s, _m, lt):
        if not _assoc_mem(_m.name, s.resolved):
            if _assoc_mem(_m.name, s.unresolved):
                raise Failure("Error [Aircraft]: cyclic dependency found when loading module " + _m.name)
            return _dep_resolve(s, _m, target, firmware, lt)
        return s

    name = m.name
    sol = replace(sol, unresolved=sol.unresolved + [(name, m)])
    if M.check_loading(target, firmware, m):
        dep = m.dependencies
        if dep is not None:
            for dep_expr in dep.requires:
                if dep_expr[0] == "var":
                    dep_name = dep_expr[1]
                    if dep_name.startswith("@"):
                        sol = replace(sol, required=sol.required + [(dep_expr, name)])
                    else:
                        _m = M.from_module_name(None, dep_name)
                        sol = test_module(sol, _m, USER_LOAD if name == "root" else DEPEND)
                else:
                    sol = replace(sol, required=sol.required + [(dep_expr, name)])
            if name == "root":
                for suggested in dep.suggests:
                    sol = test_module(sol, M.from_module_name(None, suggested), SUGGESTED)
            sol = replace(sol, conflicts=sol.conflicts + [(c, name) for c in dep.conflicts])
            sol = replace(sol, provided=sol.provided + [(p, name) for p in dep.provides])
            _add_suggested(sol, dep)
        if name != "root":
            sol = replace(sol, resolved=sol.resolved + [(name, (load_type, m))])
    else:
        if m.dependencies is not None:
            _add_suggested(sol, m.dependencies)
        sol = replace(sol, unloaded=sol.unloaded + [(UNLOADED, m)])
    return replace(sol, unresolved=_remove_assoc(name, sol.unresolved))


def _dep_order(sol):
    def extract_dep(l, e):
        k = e[0]
        if k in ("any", "not"):
            return l
        if k in ("and", "or"):
            return extract_dep(extract_dep(l, e[1]), e[2])
        s = e[1]
        if s.startswith("@"):
            acc = l
            for f, m_name in sol.provided:
                if f == s:
                    acc = [m_name] + acc
            return acc
        return [s] + l

    dep_list = []
    for n, (_, m) in sol.resolved:
        if m.dependencies is not None:
            names = []
            for e in m.dependencies.requires + m.dependencies.recommends:
                names = extract_dep(names, e)
            names = GC.singletonize(names)
        else:
            names = []
        dep_list.append((n, names))

    flat = []
    for mname, dep in dep_list:
        flat += [mname] + dep
    all_ = GC.singletonize(flat)

    def get_deps(x):
        try:
            return _assoc(x, dep_list)
        except NotFound:
            return []

    starts = [x for x in all_ if get_deps(x) == []]
    todo = [x for x in all_ if get_deps(x) != []]

    # emulate: order acc later todo progress (OCaml lists, head at index 0)
    later, progress = [], False
    acc_ocaml = list(starts)
    while True:
        if not todo and not later:
            res = list(reversed(acc_ocaml))
            break
        if not todo:
            if progress:
                todo, later, progress = list(later), [], False
                continue
            fail_s = "".join(f" '{m}'" for m in later)
            raise Failure(f"Error [Aircraft]: un-orderable modules {fail_s}")
        x, todo = todo[0], todo[1:]
        deps = get_deps(x)
        if all(d in acc_ocaml for d in deps):
            acc_ocaml = [x] + acc_ocaml
            progress = True
        else:
            later = [x] + later

    resolved = []
    for mname in res:
        try:
            resolved.insert(0, (mname, _assoc(mname, sol.resolved)))
        except NotFound:
            pass
    return replace(sol, resolved=list(reversed(resolved)))


def resolve_modules_dep(config_by_target, firmware, user_target):
    for target, conf in list(config_by_target.items()):
        target_module_name = _concat(env.paparazzi_conf, "modules", "targets", target + ".xml")
        target_module = [GC.Var(target_module_name)] if os.path.exists(target_module_name) else []
        firmware_module_name = _concat(env.paparazzi_conf, "modules", "firmwares", conf.firmware_name + ".xml")
        firmware_module = [GC.Var(firmware_module_name)] if os.path.exists(firmware_module_name) else []
        board_module_name = _concat(env.paparazzi_conf, "modules", "boards", conf.board_type + ".xml")
        board_module = [GC.Var(board_module_name)] if os.path.exists(board_module_name) else []
        root_dep = M.Dependencies(requires=[GC.Var(m.name) for _, m in conf.modules]
                                  + target_module + firmware_module + board_module)
        root_module = replace(M.empty(), name="root", dependencies=root_dep,
                              makefiles=[M.empty_makefile()])
        solution = _dep_resolve(SortResult(), root_module, target, firmware, USER_LOAD)

        def resolve_suggested(sol, suggested):
            if user_target != "" and target == user_target:
                for c, cname in sol.conflicts:
                    for n, _ in sol.resolved:
                        if n == c:
                            raise Failure(f"Error [Aircraft]: find conflict with module '{cname}' while loading '{n}' in target '{target}'")
                    for n, _ in sol.provided:
                        if n == c:
                            raise Failure(f"Error [Aircraft]: find conflict with funcionality while loading '{n}' for '{cname}' in target '{target}'")
            modules_and_func = [p for p, _ in sol.provided] + [n for n, _ in sol.resolved]
            selection = list(suggested)
            for r, name in sol.required:
                if any(GC.eval_bool(p, r) for p in modules_and_func):
                    continue
                select = [m for p, m in sol.suggested if GC.eval_bool(p, r)]
                if not select and user_target != "" and target == user_target:
                    raise Failure(f"Error [Aircraft]: functionality '{GC.sprint_expr(r)}' is not provided for '{name}' in target '{target}'")
                selection = selection + select
            selection = GC.singletonize(selection, key=lambda m: m.name)
            if selection:
                rd = replace(root_dep, suggests=[m.name for m in selection])
                rm = replace(root_module, dependencies=rd)
                sol = _dep_resolve(SortResult(), rm, target, firmware, USER_LOAD)
            if len(suggested) == len(selection):
                return sol
            return resolve_suggested(sol, selection)

        solution = resolve_suggested(solution, [])
        solution = _dep_order(solution)
        new_conf = replace(conf, modules=solution.unloaded)
        for _, (lt, m) in solution.resolved:
            new_conf = target_conf_add_module_config(new_conf, target, firmware, m, lt)
        config_by_target.replace(target, new_conf)


def sort_airframe_by_target(config_by_target, airframe):
    if airframe is None:
        return []
    l = []
    for f in airframe.firmwares:
        for t in f.targets:
            l.append((t, f))
    for t, f in l:
        name = t.name
        if config_by_target.mem(name):
            raise Failure("Error [Aircraft]: two targets with the same name")
        conf = init_target_conf(f.name, t.board)
        conf = replace(conf, configures=t.configures + f.configures, defines=t.defines + f.defines)

        def add(c, m_af):
            c = replace(c, configures=c.configures + m_af.configures, defines=c.defines + m_af.defines)
            return target_conf_add_module(c, name, f.name, m_af.name, m_af.mtype, USER_LOAD)

        for m_af in t.modules:
            conf = add(conf, m_af)
        for m_af in f.modules:
            conf = add(conf, m_af)
        for om in airframe.modules:
            for m_af in om.modules:
                conf = add(conf, m_af)
        config_by_target.add(name, conf)
    return l


def get_config_element(flag, ac_xml, elt, f):
    if not flag:
        return None
    try:
        file = X.attrib(ac_xml, elt)
        return f(_concat(env.paparazzi_conf, file))
    except X.NoAttribute:
        return None


def get_element_relative_path(flag, ac_xml, elt):
    if not flag:
        return None
    try:
        return X.attrib(ac_xml, elt)
    except X.NoAttribute:
        return None


def get_loaded_modules(config_by_target, target):
    if not config_by_target.mem(target):
        return [], []
    lt, lm = [], []
    for t, m in config_by_target.find(target).modules:
        if t != UNLOADED:
            lt.append(t)
            lm.append(m)
    return lt, lm


def get_all_modules(config_by_target):
    modules = []
    for _, conf in config_by_target.items():
        for _, m in conf.modules:
            if not any(m.name == n.name for n in modules):
                modules.insert(0, m)
    return sorted(modules, key=lambda m: m.name)


def _hd_split_tilde(name):
    parts = [x for x in name.split("~") if x != ""]
    if not parts:
        raise Failure("hd")
    return parts[0]


def parse_aircraft(target, aircraft_xml, parse_af=False, parse_ap=False, parse_fp=False,
                   parse_rc=False, parse_tl=False, parse_set=False, parse_all=False, verbose=False):
    name = ext_xml.attrib(aircraft_xml, "name")
    conf_aircraft = []
    config_by_target = Hashtbl(5)

    if verbose:
        _out("Parsing airframe")
    airframe = get_config_element(parse_af or parse_all, aircraft_xml, "airframe", Af.from_file)
    if verbose:
        _out(" '%s'" % ("None" if airframe is None else airframe.filename))
    if airframe is not None:
        conf_aircraft.append(airframe.xml)
    if verbose:
        _out(", sorting by target")
    target_list = sort_airframe_by_target(config_by_target, airframe)
    firmware = "none"
    for t, f in target_list:
        if t.name == target:
            firmware = f.name
            break
    if verbose:
        _out(", extracting and parsing autopilot...")
    autopilots = None
    if (parse_ap or parse_all) and airframe is not None:
        aps = _collect_autopilots(airframe, target)
        if aps:
            res = []
            for af_ap in aps:
                filename = af_ap.name
                if not filename.endswith(".xml"):
                    filename += ".xml"
                filename = _concat(env.paparazzi_conf, "autopilot", filename)
                ap = Ap.from_file(filename)
                for tgt, c in list(config_by_target.items()):
                    for m in ap.modules:
                        c = replace(c, configures=c.configures + m.configures, defines=c.defines + m.defines)
                        c = target_conf_add_module(c, tgt, firmware, m.name, m.mtype, USER_LOAD)
                    config_by_target.replace(tgt, c)
                res.append((af_ap.freq, ap))
            if config_by_target.mem(target):
                config_by_target.replace(target, replace(config_by_target.find(target), autopilot=True))
            else:
                for t, _ in target_list:
                    c = config_by_target.find(t.name)
                    config_by_target.replace(target, replace(c, autopilot=True))
            autopilots = res
    if autopilots is not None:
        conf_aircraft += [ap.xml for _, ap in autopilots]
    if verbose:
        _out(" done.\n")

    if verbose:
        _out("Parsing flight plan")
    flight_plan = get_config_element(parse_fp or parse_all, aircraft_xml, "flight_plan", Fp.from_file)
    if verbose:
        _out(" '%s'" % ("None" if flight_plan is None else flight_plan.filename))
        _out(", extracting modules...")
    if flight_plan is not None:
        for tgt, c in list(config_by_target.items()):
            for m in flight_plan.modules:
                c = replace(c, configures=c.configures + m.configures, defines=c.defines + m.defines)
                c = target_conf_add_module(c, tgt, firmware, m.name, m.mtype, USER_LOAD)
            config_by_target.replace(tgt, c)
        conf_aircraft.append(flight_plan.xml)
    if verbose:
        _out(" done\n")

    if verbose:
        _out("Parsing radio")
    radio = get_config_element(parse_rc or parse_all, aircraft_xml, "radio", Rd.from_file)
    if verbose:
        _out(" '%s'..." % ("None" if radio is None else radio.filename))
    if radio is not None:
        conf_aircraft.append(radio.xml)
    if verbose:
        _out(" done\n")

    if verbose:
        _out("Parsing telemetry")
    telemetry = get_config_element(parse_tl or parse_all, aircraft_xml, "telemetry", Tl.from_file)
    if verbose:
        _out(" '%s'..." % ("None" if telemetry is None else telemetry.filename))
    if telemetry is not None:
        conf_aircraft.append(telemetry.xml)
    if verbose:
        _out(" done\n")

    resolve_modules_dep(config_by_target, firmware, target)
    loaded_types, loaded_modules = get_loaded_modules(config_by_target, target)
    all_modules = get_all_modules(config_by_target)

    if verbose:
        _out("Parsing settings...")
    settings = None
    if parse_set or parse_all:
        try:
            s = env.filter_settings(ext_xml.attrib(aircraft_xml, "settings"))
        except Exception:
            s = ""
        files = [x for x in s.split(" ") if x != ""]
        normal = [S.from_file(_concat(env.paparazzi_conf, f)) for f in files]
        try:
            sm = env.filter_settings(ext_xml.attrib(aircraft_xml, "settings_modules"))
        except Exception:
            sm = ""
        sm_files = [x for x in sm.split(" ") if x != ""]
        settings_modules = []
        for m in loaded_modules:
            def match(nm):
                nm = _hd_split_tilde(nm)
                full = nm if nm.startswith("/") else _concat(env.paparazzi_conf, nm)
                return m.xml_filename == full
            if any(match(nm) for nm in sm_files):
                settings_modules = settings_modules + m.settings
        sys_tl = Tl.get_sys_telemetry_settings(telemetry)
        sys_mod = M.get_sys_modules_settings(loaded_modules)
        sys_fp = Fp.get_sys_fp_settings(flight_plan)
        sys_ap = Ap.get_sys_ap_settings(autopilots)
        system = [x for x in (sys_tl, sys_fp, sys_mod, sys_ap) if x is not None]
        sys_dl = []
        for s_ in system:
            sys_dl = sys_dl + s_.dl_settings
        sys_dl_settings = S.DlSettings(name="System", dl_settings=sys_dl, dl_setting=[], headers=[],
                                       xml=X.Element("dl_settings", [("name", "System")], [s_.xml for s_ in sys_dl]))
        system_settings = S.Settings(filename="", name=None, target=None, dl_settings=[sys_dl_settings],
                                     xml=X.Element("settings", [], [X.Element("dl_settings", [], [sys_dl_settings.xml])]))
        allset = [system_settings] + normal + settings_modules
        settings = [s_ for s_ in allset if GC.test_targets(target, GC.bool_expr_of_string(s_.target))]
        conf_aircraft.append(S.get_settings_xml(settings))
    if verbose:
        _out(" done\n")

    if verbose:
        _out("Loading modules:\n")
        for lt, m in zip(loaded_types, loaded_modules):
            _out(" - (%s) %s (%s) [%s]\n" % (lt, m.name, m.dir or "", m.xml_filename))

    return Aircraft(name=name, config_by_target=config_by_target, all_modules=all_modules,
                    airframe=airframe, autopilots=autopilots, flight_plan=flight_plan,
                    radio=radio, telemetry=telemetry, settings=settings, xml=conf_aircraft)


def _collect_autopilots(af, target):
    """List.fold_left over firmwares starting from af.autopilots:
       acc' = (target autopilots prepended to firmware autopilot) @ acc"""
    acc = list(af.autopilots)
    for f in af.firmwares:
        ap_t = [] if f.autopilot is None else [f.autopilot]
        for t in f.targets:
            if t.name == target and t.autopilot is not None:
                ap_t = [t.autopilot] + ap_t
        acc = ap_t + acc
    return acc
