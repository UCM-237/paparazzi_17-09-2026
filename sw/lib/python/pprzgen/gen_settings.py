"""Port of sw/tools/generators/gen_settings.ml: generates settings.h/.xml"""

import re
import sys

from . import xml_light as X
from . import ext_xml
from . import settings as S
from .xml2h import define_out, begin_out, finish_out, open_out
from .ocaml import Failure, InvalidArgument


def ocaml_basename(name):
    """Filename.basename (Unix generic_basename)."""
    if name == "":
        return "."
    n = len(name) - 1
    while n >= 0 and name[n] == "/":
        n -= 1
    if n < 0:
        return name[0:1]
    p = n + 1
    while n >= 0 and name[n] != "/":
        n -= 1
    return name[n + 1:p]


class _P:
    def __init__(self, out):
        self.out = out
        self.margin = 0

    def right(self):
        self.margin += 2

    def left(self):
        self.margin -= 2

    def lprintf(self, s):
        self.out.write(" " * self.margin + s)


def flatten(xml, r):
    if ext_xml.tag_is(xml, "dl_setting"):
        return [xml] + r
    kids = X.children(xml)
    if not kids:
        return r
    x = kids[0]
    for y in kids[1:]:
        if not ext_xml.tag_is(y, X.tag(x)):
            raise AssertionError("gen_settings.ml: flatten")
    for c in reversed(kids):  # List.fold_right
        r = flatten(c, r)
    return r


def _str_split(regex, s):
    parts = re.split(regex, s)
    while parts and parts[0] == "":
        parts.pop(0)
    while parts and parts[-1] == "":
        parts.pop()
    return parts


def _exit1(msg):
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()
    raise SystemExit(1)


def print_dl_settings(p, settings, settings_xml):
    out = p.out
    settings_xml = flatten(settings_xml, [])
    p.lprintf("\n")
    for s in settings:
        for h in S.get_headers(s):
            p.lprintf('#include "%s.h"\n' % h)
    p.lprintf('#include "generated/modules.h"\n')
    p.lprintf("\n")
    define_out(out, "SETTINGS_NAMES", "{ \\")
    for b in settings_xml:
        out.write(' { "%s" }, \\\n' % ext_xml.attrib(b, "var"))
    p.lprintf("};\n")
    define_out(out, "SETTINGS_NAMES_SHORT", "{ \\")
    for b in settings_xml:
        varname = _str_split(r"[_.]+", ext_xml.attrib(b, "var"))
        shortname = ""
        for c in varname:
            shortname = shortname + "_" + (c[:3] if len(c) >= 3 else c)
        if len(shortname) >= 17:
            shorted = shortname[1:17]
        elif len(shortname) >= 1:
            shorted = shortname[1:]
        else:
            raise InvalidArgument("String.sub / Bytes.sub")
        out.write(' "%s" , \\\n' % shorted)
    p.lprintf("};\n")
    define_out(out, "NB_SETTING", str(len(settings_xml)))
    p.lprintf("#define DlSetting(_idx, _value) { \\\n")
    p.right()
    p.lprintf("switch (_idx) { \\\n")
    p.right()
    idx = 0
    for s in settings_xml:
        v = ext_xml.attrib(s, "var")
        try:
            h = ext_xml.attrib(s, "handler")
        except ext_xml.ExtXmlError:
            p.lprintf("case %d: %s = _value; break;\\\n" % (idx, v))
        else:
            t = ext_xml.attrib_or_default(s, "type", "")
            m1 = ext_xml.attrib_or_default(s, "module", "")
            m2 = ext_xml.attrib_or_default(s, "header", "")
            if m1 == "" and m2 == "":
                _exit1("Error: You need to specify the header (or module for legacy) attribute for setting %s to use the handler %s" % (v, h))
            elif m1 == "" and t == "fun":
                p.lprintf("case %d: %s_%s( _value ); break;\\\n" % (idx, ocaml_basename(m2), h))
            elif m2 == "" and t == "fun":
                p.lprintf("case %d: %s_%s( _value ); break;\\\n" % (idx, ocaml_basename(m1), h))
            elif m1 == "":
                p.lprintf("case %d: %s_%s( _value ); _value = %s; break;\\\n" % (idx, ocaml_basename(m2), h, v))
            elif m2 == "":
                p.lprintf("case %d: %s_%s( _value ); _value = %s; break;\\\n" % (idx, ocaml_basename(m1), h, v))
            else:
                _exit1("Error: You can't specify both module and header attributes for setting %s to use the handler %s" % (v, h))
        idx += 1
    p.lprintf("default: break;\\\n")
    p.left()
    p.lprintf("}\\\n")
    p.left()
    p.lprintf("}\n")
    nb_values = idx
    p.lprintf("#define PeriodicSendDlValue(_trans, _dev) { \\\n")
    if nb_values > 0:
        p.right()
        p.lprintf("static uint8_t i;\\\n")
        p.lprintf("float var;\\\n")
        p.lprintf("if (i >= %d) i = 0;\\\n" % nb_values)
        p.lprintf("switch (i) { \\\n")
        p.right()
        for i, s in enumerate(settings_xml):
            t = ext_xml.attrib_or_default(s, "type", "")
            v = ext_xml.attrib(s, "var")
            if t == "fun":
                p.lprintf("case %d: var = 0; break;\\\n" % i)
            else:
                p.lprintf("case %d: var = %s; break;\\\n" % (i, v))
        p.lprintf("default: var = 0.; break;\\\n")
        p.left()
        p.lprintf("}\\\n")
        p.lprintf("pprz_msg_send_DL_VALUE(_trans, _dev, AC_ID, &i, &var);\\\n")
        p.lprintf("i++;\\\n")
        p.left()
    p.lprintf("}\n")
    p.lprintf("static inline float settings_get_value(uint8_t i) {\n")
    p.right()
    p.lprintf("switch (i) {\n")
    p.right()
    for i, s in enumerate(settings_xml):
        t = ext_xml.attrib_or_default(s, "type", "")
        v = ext_xml.attrib(s, "var")
        if t == "fun":
            p.lprintf("case %d: return 0;\n" % i)
        else:
            p.lprintf("case %d: return %s;\n" % (i, v))
    p.lprintf("default: return 0.;\n")
    p.left()
    p.lprintf("}\n")
    p.left()
    p.lprintf("}\n")


_INTTYPES = {"bool": "uint8_t", "int8": "int8_t", "int16": "int16_t", "int32": "int32_t",
             "int64": "int64_t", "uint8": "uint8_t", "uint16": "uint16_t", "uint32": "uint32_t",
             "uint64": "uint64_t", "float": "float", "double": "double"}


def inttype(x):
    if x not in _INTTYPES:
        raise Failure("Gen_calib.inttype: unknown type '%s'" % x)
    return _INTTYPES[x]


def print_persistent_settings(p, settings, settings_xml):
    settings_xml = flatten(settings_xml, [])

    def has_persistent(x):
        try:
            X.attrib(x, "persistent")
            return True
        except Exception:
            return False

    pers = [x for x in settings_xml if has_persistent(x)]
    p.lprintf("\n/* Persistent Settings */\n")
    p.lprintf("struct PersistentSettings {\n")
    p.right()
    for i, s in enumerate(pers):
        v = ext_xml.attrib(s, "var")
        try:
            t = ext_xml.attrib(s, "type")
        except Exception:
            t = "float"
        p.lprintf("%s s_%d; /* %s */\n" % (inttype(t), i, v))
    p.left()
    p.lprintf("};\n\n")
    p.lprintf("extern struct PersistentSettings pers_settings;\n\n")
    p.lprintf("static inline void persistent_settings_store( void ) {\n")
    p.right()
    for i, s in enumerate(pers):
        p.lprintf("pers_settings.s_%d = %s;\n" % (i, ext_xml.attrib(s, "var")))
    p.left()
    p.lprintf("}\n\n")
    p.lprintf("static inline void persistent_settings_load( void ) {\n")
    p.right()
    for i, s in enumerate(pers):
        v = ext_xml.attrib(s, "var")
        try:
            h = ext_xml.attrib(s, "handler")
            m = ext_xml.attrib(s, "module")
            p.lprintf("%s_%s( pers_settings.s_%d );\n" % (ocaml_basename(m), h, i))
        except ext_xml.ExtXmlError:
            p.lprintf("%s = pers_settings.s_%d;\n" % (v, i))
    p.left()
    p.lprintf("}\n")


H_NAME = "SETTINGS_H"


def generate(settings, xml_files, out_xml, out_file):
    with open_out(out_file) as out:
        xml = S.get_settings_xml(settings)
        with open_out(out_xml) as f:
            f.write(ext_xml.to_string_fmt(xml) + "\n")
        begin_out(out, " ".join(xml_files), H_NAME)
        p = _P(out)
        print_dl_settings(p, settings, xml)
        print_persistent_settings(p, settings, xml)
        finish_out(out, H_NAME)
