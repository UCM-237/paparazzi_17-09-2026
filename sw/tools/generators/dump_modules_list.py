#!/usr/bin/env python3
"""List the modules of an aircraft (Python replacement of dump_modules_list.out).

Usage: dump_modules_list.py -ac NAME [-af airframe.xml -fp flight_plan.xml] [-t target] [-o file]
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "lib", "python"))

from pprzgen import xml_light as X, ext_xml, env  # noqa: E402
from pprzgen import aircraft as AC  # noqa: E402
from pprzgen.ocaml import Failure, concat as _concat  # noqa: E402


def main(argv):
    opts = {"-ac": None, "-af": None, "-fp": None, "-t": None, "-o": None}
    i = 1
    while i < len(argv):
        if argv[i] in opts and i + 1 < len(argv):
            opts[argv[i]] = argv[i + 1]
            i += 2
        else:
            i += 1
    ac_name, af, fp = opts["-ac"], opts["-af"], opts["-fp"]
    if ac_name is None:
        raise Failure("Dump modules: provide aircraft name")
    if (af is None) != (fp is None):
        raise Failure("Dump modules: provide both airframe and flight plan or nothing")
    if af is None:
        conf_xml = ext_xml.parse_file(_concat(env.paparazzi_home, "conf", "conf.xml"))
        for a in X.children(conf_xml):
            if ext_xml.attrib(a, "name") == ac_name:
                aircraft_xml = a
                break
        else:
            raise Failure("Dump modules: aircraft name not found in conf.xml")
    else:
        aircraft_xml = X.Element("aircraft", [("name", ac_name), ("airframe", af), ("flight_plan", fp)], [])
    ac = AC.parse_aircraft("", aircraft_xml, parse_af=True, parse_ap=True, parse_fp=True)
    t = opts["-t"]
    if t is None:
        names = [m.xml_filename for m in ac.all_modules]
    else:
        if not ac.config_by_target.mem(t):
            raise Failure("Dump modules: unknown target")
        names = ["%s %s" % (m.xml_filename, lt) for lt, m in ac.config_by_target.find(t).modules]
    text = "\n".join(names) + "\n"
    if opts["-o"] is None:
        sys.stdout.write(text)
    else:
        with open(opts["-o"], "w", encoding="latin-1", newline="") as f:
            f.write(text)


if __name__ == "__main__":
    try:
        main(sys.argv)
    except Failure as e:
        sys.stderr.write('Fatal error: exception Failure("%s")\n' % e)
        sys.exit(2)
    except Exception as e:
        sys.stderr.write("Fatal error: exception %s(%r)\n" % (type(e).__name__, str(e)))
        sys.exit(2)
