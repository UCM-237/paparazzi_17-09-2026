"""Port of sw/lib/ocaml/xml2h.ml: helpers to write C headers."""

import sys

from . import env, ext_xml
from .ocaml import Failure


def open_out(path):
    """Text file with OCaml-like byte semantics (Latin-1, no newline mapping)."""
    return open(path, "w", encoding="latin-1", newline="")


def define_out(out, n, x):
    if x == "":
        out.write(f"#define {n}\n")
    else:
        out.write(f"#define {n} {x}\n")


def define_string_out(out, n, x):
    define_out(out, n, '"' + x + '"')


def xml_error(s):
    raise Failure("Bad XML tag: " + s + " expected")


def sprint_float_array(lst):
    return "{" + ",".join(lst) + "}"


def begin_out(out, xml_file, h_name):
    out.write(f"/* This file has been generated from {xml_file} */\n")
    out.write(f"/* Version {env.get_paparazzi_version()} */\n")
    out.write("/* Please DO NOT EDIT */\n\n")
    out.write(f"#ifndef {h_name}\n")
    out.write(f"#define {h_name}\n\n")


def start_and_begin_out(out, xml_file, h_name):
    xml = ext_xml.parse_file(xml_file)
    begin_out(out, xml_file, h_name)
    return xml


def begin_c_out(out, xml_file, name):
    out.write(f"/* This file has been generated from {xml_file} */\n")
    out.write(f"/* Version {env.get_paparazzi_version()} */\n")
    out.write("/* Please DO NOT EDIT */\n\n")
    out.write(f'#include "{name}.h"\n\n')


def finish_out(out, h_name):
    out.write(f"\n#endif // {h_name}\n")


def warning(s):
    sys.stderr.write("##################################################\n")
    sys.stderr.write(f" {s}\n")
    sys.stderr.write("##################################################\n")
