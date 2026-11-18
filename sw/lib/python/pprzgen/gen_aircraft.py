"""Port of sw/tools/generators/gen_aircraft.ml: main aircraft generator.

Exit codes follow the OCaml program: 0 ok, 1 for reported errors
(Failure / explicit exit 1), 2 for uncaught exceptions.
"""

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

from .ocaml import concat as _concat
from . import xml_light as X
from . import ext_xml, env, pprzlink
from . import aircraft as AC
from . import gen_airframe, gen_flight_plan, gen_autopilot, gen_radio, gen_periodic
from . import gen_modules, gen_settings, gen_makefile
from .ocaml import Failure, NotFound, Hashtbl

AIRFRAME_H = "airframe.h"
FLIGHT_PLAN_H = "flight_plan.h"
FLIGHT_PLAN_XML = "flight_plan.xml"
RADIO_H = "radio.h"
PERIODIC_H = "periodic_telemetry.h"
MODULES_H = "modules.h"
SETTINGS_H = "settings.h"
SETTINGS_XML = "settings.xml"


def out(s):
    sys.stdout.write(s)
    sys.stdout.flush()


def mkdir(d):
    os.makedirs(d, exist_ok=True)


def check_unique_id_and_name(conf, conf_xml):
    ids, names = {}, {}
    for x in X.children(conf):
        if not ext_xml.tag_is(x, "aircraft"):
            continue
        id_ = ext_xml.attrib(x, "ac_id")
        name = ext_xml.attrib(x, "name")
        if id_ in ids:
            raise Failure("Error: A/C Id '%s' duplicated in %s (%s and %s)" % (id_, conf_xml, name, ids[id_]))
        if name in names:
            raise Failure("Error: A/C name '%s' duplicated in %s (ids %s and %s)" % (name, conf_xml, id_, names[name]))
        ids[id_] = name
        names[name] = id_


def is_older(target_file, dep_files):
    if not os.path.exists(target_file):
        return True
    t = os.stat(target_file).st_mtime
    return any(t < os.stat(f).st_mtime for f in dep_files)


def generate_config_element(elt, f, dep_list, verbose=True):
    if any(is_older(file, dep) for file, dep in dep_list):
        if verbose:
            out("(updated)")
        f(elt)
    elif verbose:
        out("(unchanged)")


def copy_file(source, dest):
    if subprocess.run(["sh", "-c", "cp %s %s" % (source, dest)]).returncode != 0:
        raise AssertionError("gen_aircraft.ml: copy_file")


def parse_args(argv):
    opts = dict(name=None, target=None, conf=_concat(env.paparazzi_conf, "conf.xml"),
                ac_conf=False, airframe=False, flight_plan=False, settings=False, radio=False,
                telemetry=False, autopilot=False, all=False, ac_dir=None, conf_dir=None)
    flags = {"-ac_conf": "ac_conf", "-airframe": "airframe", "-flight_plan": "flight_plan",
             "-settings": "settings", "-radio": "radio", "-telemetry": "telemetry",
             "-autopilot": "autopilot", "-all": "all"}
    strings = {"-name": "name", "-target": "target", "-conf": "conf", "-ac_dir": "ac_dir",
               "-conf_dir": "conf_dir"}
    i = 1
    while i < len(argv):
        a = argv[i]
        if a in flags:
            opts[flags[a]] = True
        elif a in strings:
            if i + 1 >= len(argv):
                sys.stderr.write("%s: option '%s' needs an argument.\n" % (argv[0], a))
                raise SystemExit(2)
            opts[strings[a]] = argv[i + 1]
            i += 1
        elif a in ("-help", "--help"):
            sys.stdout.write("Usage: \n")
            for k in list(strings) + list(flags):
                sys.stdout.write("  %s\n" % k)
            raise SystemExit(0)
        else:
            sys.stderr.write("%s: Warning: Don't do anything with '%s' argument\n" % (argv[0], a))
        i += 1
    return opts


def main(argv):
    o = parse_args(argv)
    if o["name"] is None:
        raise Failure("An aircraft name is mandatory")
    aircraft = o["name"]
    if o["target"] is None:
        raise Failure("A target name is mandatory")
    target = o["target"]
    out("Aircraft generator: '%s' for target '%s'\n" % (aircraft, target))
    try:
        run(o, aircraft, target)
    except Failure as e:
        sys.stdout.flush()
        sys.stderr.write(str(e) + "\n")
        sys.stderr.flush()
        raise SystemExit(1)


def run(o, aircraft, target):
    conf_xml = o["conf"]
    conf = ext_xml.parse_file(conf_xml)
    check_unique_id_and_name(conf, conf_xml)
    try:
        aircraft_xml = ext_xml.child(conf, "aircraft", select=lambda x: X.attrib(x, "name") == aircraft)
    except NotFound:
        raise Failure("Aircraft '%s' not found in '%s'" % (aircraft, conf_xml))

    def value(a):
        return ext_xml.attrib(aircraft_xml, a)

    default_aircraft_dir = _concat(env.paparazzi_home, "var", "aircrafts", aircraft)
    if o["conf_dir"] is None:
        aircraft_dir, aircraft_conf_dir = default_aircraft_dir, _concat(default_aircraft_dir, "conf")
    else:
        aircraft_dir, aircraft_conf_dir = o["conf_dir"], _concat(o["conf_dir"], "conf")
    if o["ac_dir"] is None:
        aircraft_gen_dir = _concat(default_aircraft_dir, target, "generated")
    else:
        aircraft_gen_dir = _concat(o["ac_dir"], "generated")
    mkdir(aircraft_conf_dir)
    mkdir(aircraft_gen_dir)

    ac = AC.parse_aircraft(target, aircraft_xml, parse_af=o["airframe"], parse_ap=o["autopilot"],
                           parse_fp=o["flight_plan"], parse_rc=o["radio"], parse_tl=o["telemetry"],
                           parse_set=o["settings"], parse_all=o["all"], verbose=True)

    configuration = X.Element("configuration", [], [X.Element("conf", [], ac.xml), pprzlink.messages_xml()])
    conf_aircraft_file = _concat(aircraft_conf_dir, "conf_aircraft.xml")
    data = (ext_xml.to_string_fmt(configuration) + "\n").encode("latin-1")
    with open(conf_aircraft_file, "wb") as f:
        f.write(data)
    with open(conf_aircraft_file, "rb") as f:
        md5sum = hashlib.md5(f.read()).hexdigest()
    md5sum_file = _concat(aircraft_conf_dir, "aircraft.md5")
    same = False
    if os.path.exists(md5sum_file):
        with open(md5sum_file, "rb") as f:
            line = f.readline()
        if not line:
            raise EOFError("End_of_file")
        same = (md5sum == line.rstrip(b"\n").decode("latin-1"))
    if not same:
        with open(md5sum_file, "w", encoding="latin-1", newline="") as f:
            f.write(md5sum + "\n")
        d = time.localtime()
        filename = "%02d_%02d_%02d__%02d_%02d_%02d_%s_%s.conf" % (
            d.tm_year % 100, d.tm_mon, d.tm_mday, d.tm_hour, d.tm_min, d.tm_sec, md5sum, aircraft)
        vd = _concat(env.paparazzi_home, "var", "conf")
        mkdir(vd)
        with open(_concat(vd, filename), "wb") as f:
            f.write(data)
        if subprocess.run(["sh", "-c", "gzip -f " + _concat(vd, filename)]).returncode != 0:
            raise AssertionError("ocaml_tools.ml: compress")

    # flight plan
    out("Dumping flight plan XML and header...")
    abs_flight_plan_h = _concat(aircraft_gen_dir, FLIGHT_PLAN_H)
    abs_flight_plan_dump = _concat(aircraft_dir, FLIGHT_PLAN_XML)
    fp = ac.flight_plan
    if fp is None:
        out("(skip)")
    else:
        generate_config_element(fp, lambda e: gen_flight_plan.generate(e, fp.filename, abs_flight_plan_h),
                                [(abs_flight_plan_h, [fp.filename])])
        generate_config_element(fp, lambda e: gen_flight_plan.generate(e, fp.filename, abs_flight_plan_dump, dump=True),
                                [(abs_flight_plan_dump, [fp.filename])], verbose=False)
        f = AC.get_element_relative_path(o["flight_plan"] or o["all"], aircraft_xml, "flight_plan")
        if f is not None:
            d = _concat(aircraft_conf_dir, gen_flight_plan.ocaml_dirname(f))
            mkdir(d)
            copy_file(fp.filename, d)
    out(" done\n")

    # airframe
    out("Dumping airframe header...")
    abs_airframe_h = _concat(aircraft_gen_dir, AIRFRAME_H)
    af = ac.airframe
    if af is None:
        out("(skip)")
    else:
        inc = [i.href.replace("$AC_ID", value("ac_id")) for i in af.includes]
        generate_config_element(af, lambda e: gen_airframe.generate(e, value("ac_id"), aircraft, md5sum,
                                                                    af.filename, abs_airframe_h),
                                [(abs_airframe_h, [af.filename] + inc)])
        f = AC.get_element_relative_path(o["airframe"] or o["all"], aircraft_xml, "airframe")
        if f is not None:
            d = _concat(aircraft_conf_dir, gen_flight_plan.ocaml_dirname(f))
            mkdir(d)
            copy_file(af.filename, d)
            copy_file(_concat(env.paparazzi_conf, "airframes", "airframe.dtd"),
                      _concat(aircraft_conf_dir, "airframes"))
    out(" done\n")

    # autopilot
    out("Dumping autopilot header...")
    if ac.autopilots is None:
        out("(skip)")
    else:
        for freq, autopilot in ac.autopilots:
            dep = [(_concat(aircraft_gen_dir, "autopilot_core_" + X.attrib(sm, "name") + ".h"),
                    [autopilot.filename]) for sm in X.children(autopilot.xml)]
            generate_config_element(autopilot, lambda e, freq=freq: gen_autopilot.generate(e, freq, aircraft_gen_dir), dep)
    out(" done\n")

    # radio
    out("Dumping radio header...")
    abs_radio_h = _concat(aircraft_gen_dir, RADIO_H)
    if ac.radio is None:
        out("(skip)")
    else:
        generate_config_element(ac.radio, lambda e: gen_radio.generate(e, ac.radio.filename, abs_radio_h),
                                [(abs_radio_h, [ac.radio.filename])])
    out(" done\n")

    # telemetry
    out("Dumping telemetry header...")
    abs_telemetry_h = _concat(aircraft_gen_dir, PERIODIC_H)
    if ac.telemetry is None:
        out("(skip)")
    else:
        generate_config_element(ac.telemetry, lambda e: gen_periodic.generate(e, abs_telemetry_h),
                                [(abs_telemetry_h, [ac.telemetry.filename])])
    out(" done\n")

    # modules
    out("Dumping modules header...")
    _, loaded_modules = AC.get_loaded_modules(ac.config_by_target, target)
    abs_modules_h = _concat(aircraft_gen_dir, MODULES_H)
    generate_config_element(loaded_modules, lambda e: gen_modules.generate(e, "", abs_modules_h),
                            [(abs_modules_h, [m.xml_filename for m in loaded_modules])])
    out(" done\n")

    # settings
    out("Dumping settings XML and header...")
    abs_settings_h = _concat(aircraft_gen_dir, SETTINGS_H)
    abs_settings_xml = _concat(aircraft_dir, SETTINGS_XML)
    if ac.settings is None:
        out("(skip)")
    else:
        dep_list = []
        for s in ac.settings:
            if os.path.exists(s.filename):
                dep_list.insert(0, s.filename)
        generate_config_element(ac.settings, lambda e: gen_settings.generate(e, dep_list, abs_settings_xml, abs_settings_h),
                                [(abs_settings_h, dep_list)])
    out(" done\n")

    # makefile
    fd, temp_makefile_ac = tempfile.mkstemp(prefix="Makefile.ac", suffix="tmp")
    os.close(fd)
    if ac.airframe is not None and ac.flight_plan is not None:
        gen_makefile.generate_makefile(value("ac_id"), ac.config_by_target, temp_makefile_ac)
    else:
        sys.stderr.write("Missing airframe or flight_plan")
        sys.stderr.flush()
    out("Dumping makefile...")
    makefile_ac = _concat(aircraft_dir, "Makefile.ac")
    if ac.airframe is None:
        out("(skip)")
    else:
        module_files = [m.xml_filename for m in loaded_modules]
        if is_older(makefile_ac, [ac.airframe.filename] + module_files):
            out("(copying)")
            shutil.move(temp_makefile_ac, makefile_ac)
        else:
            out("(skip copying)")
        out(" done\n")


def entry():
    try:
        main(sys.argv)
    except SystemExit:
        raise
    except Failure as e:  # failures before the main `try` (missing options)
        sys.stdout.flush()
        sys.stderr.write("Fatal error: exception Failure(\"%s\")\n" % e)
        sys.stderr.flush()
        raise SystemExit(2)
    except BaseException as e:
        sys.stdout.flush()
        sys.stderr.write("Fatal error: exception %s(%r)\n" % (type(e).__name__, str(e)))
        sys.stderr.flush()
        raise SystemExit(2)
    sys.stdout.flush()
    raise SystemExit(0)
