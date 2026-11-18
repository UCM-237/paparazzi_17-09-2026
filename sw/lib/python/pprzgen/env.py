"""Port of sw/lib/ocaml/env.ml (paths and environment)."""

import os
import re
import subprocess

from .ocaml import concat as _concat

paparazzi_src = os.environ.get("PAPARAZZI_SRC", "/usr/share/paparazzi")
paparazzi_home = os.environ.get("PAPARAZZI_HOME") or _concat(os.environ.get("HOME", ""), "paparazzi")
if "PAPARAZZI_HOME" in os.environ:
    paparazzi_home = os.environ["PAPARAZZI_HOME"]

paparazzi_conf = _concat(paparazzi_home, "conf")
flight_plans_path = _concat(paparazzi_conf, "flight_plans")
flight_plan_dtd = _concat(flight_plans_path, "flight_plan.dtd")
srtm_path = _concat(paparazzi_home, "data", "srtm")
srtm_pprzgcs_path = _concat(os.environ.get("HOME", ""), ".local", "share", "pprzgcs", "srtm")

default_module_targets = "ap|sim|nps|hitl"


def _modules_paths():
    default_path = _concat(paparazzi_conf, "modules")
    path = os.environ.get("PAPARAZZI_MODULES_PATH")
    if path is None:
        return [default_path]
    try:
        paths = []
        for d in [x for x in path.split(":") if x != ""]:
            sub = []
            for sdir in os.listdir(_concat(d, "modules")):
                full = _concat(d, "modules", sdir)
                if os.path.isdir(full):
                    sub.insert(0, full)
            paths += sub
        return paths + [default_path]
    except OSError:
        return [default_path]


modules_paths = _modules_paths()


def _modules_ext_paths():
    path = os.environ.get("PAPARAZZI_MODULES_PATH")
    if path is None:
        return []
    return [x for x in path.split(":") if x != ""]


modules_ext_paths = _modules_ext_paths()


def filter_absolute_path(path):
    return path.replace(_concat(paparazzi_home, "conf/"), "", 1)


def filter_settings(settings):
    sl = [s for s in re.split(r"[ ]+", settings) if s != ""]
    sl = [s for s in sl if not (s[0] == "[" and s[-1] == "]")]
    return " ".join(sl)


_version = None


def get_paparazzi_version():
    """Output of $PAPARAZZI_SRC/paparazzi_version, run in the current dir
    (cached: the OCaml code runs it each time, the result is the same)."""
    global _version
    if _version is None:
        try:
            out = subprocess.run(paparazzi_src + "/paparazzi_version", shell=True,
                                 capture_output=True).stdout.decode("latin-1")
            _version = re.sub(r"[ \n]+$", "", out, count=1)
        except Exception:
            _version = "UNKNOWN"
    return _version
