#!/usr/bin/env python3
"""XSens protocol header generator (Python replacement of gen_xsens.out).

Usage: gen_xsens.py <xsens.xml>   (header written on stdout)
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "lib", "python"))

from pprzgen import xml_light as X, ext_xml  # noqa: E402
from pprzgen.ocaml import Failure, int_of_string  # noqa: E402

_out = []


def w(s):
    _out.append(s)


SIZES = {"U32": 32, "U8": 8, "U4": 4, "I4": 4, "R4": 4, "U2": 2, "I2": 2, "U1": 1, "I1": 1}
CTYPES = {"R4": "float", "I4": "int32_t", "I2": "int16_t", "I1": "int8_t", "U4": "uint32_t",
          "U2": "uint16_t", "U1": "uint8_t"}


def sizeof(f):
    if f not in SIZES:
        raise Failure("sizeof: unknown format '%s'" % f)
    return SIZES[f]


def test_type(f):
    if f in ("U32", "U8"):
        return "skip"
    if f == "V":
        return "variable"
    return "fixe"


def c_type(f):
    if f not in CTYPES:
        raise Failure("Gen_xsens.c_type: unknown format '%s'" % f)
    return CTYPES[f]


def get_at(o, fmt, block_size):
    t = c_type(fmt)
    b = "" if block_size == 0 else "+%d*_xsens_block" % block_size
    if fmt == "R4":
        return ("({ union { uint32_t u; float f; } _f; _f.u = (uint32_t)(*((uint8_t*)_xsens_payload+3+%d%s)|*((uint8_t*)_xsens_payload+2+%d%s)<<8|((uint32_t)*((uint8_t*)_xsens_payload+1+%d%s))<<16|((uint32_t)*((uint8_t*)_xsens_payload+%d%s))<<24); _f.f; })"
                % (o, b, o, b, o, b, o, b))
    if fmt in ("U4", "I4"):
        return ("(%s)(*((uint8_t*)_xsens_payload+3+%d%s)|*((uint8_t*)_xsens_payload+2+%d%s)<<8|((%s)*((uint8_t*)_xsens_payload+1+%d%s))<<16|((%s)*((uint8_t*)_xsens_payload+%d%s))<<24)"
                % (t, o, b, o, b, t, o, b, t, o, b))
    if fmt in ("U2", "I2"):
        return "(%s)(*((uint8_t*)_xsens_payload+1+%d%s)|*((uint8_t*)_xsens_payload+%d%s)<<8)" % (t, o, b, o, b)
    if fmt in ("U1", "I1"):
        return "(%s)(*((uint8_t*)_xsens_payload+%d%s))" % (t, o, b)
    raise Failure("Gen_xsens.c_type: unknown format '%s'" % fmt)


def define(x, y):
    w("#define %s %s\n" % (x, y))


class LengthError(Exception):
    def __init__(self, m, l1, l2):
        super().__init__(l1, l2)
        self.m, self.l1, self.l2 = m, l1, l2


def _check_length(m, offset):
    try:
        l = int_of_string(X.attrib(m, "length"))
        if l != offset:
            raise LengthError(m, l, offset)
    except X.NoAttribute as e:
        if str(e) != "length":
            raise


def parse_message(m):
    msg_name = X.attrib(m, "name")
    w("\n")
    msg_id = "XSENS_%s_ID" % msg_name
    define(msg_id, X.attrib(m, "ID"))
    field_name = lambda f: ext_xml.attrib(f, "name")
    fmt_of = lambda f: X.attrib(f, "format")
    offset = [0]

    def gen_read_macro(block_size, f):
        t = X.tag(f)
        if t == "field":
            fn = field_name(f)
            fmt = fmt_of(f)
            tt = test_type(fmt_of(f))
            if tt == "fixe":
                block_no = "" if block_size == 0 else ",_xsens_block"
                define("XSENS_%s_%s(_xsens_payload%s)" % (msg_name, fn, block_no), get_at(offset[0], fmt, block_size))
                offset[0] += sizeof(fmt)
            elif tt == "variable":
                w("/* XSENS_%s_%s: variable data size */\n" % (msg_name, fn))
            else:
                offset[0] += sizeof(fmt)
        elif t == "block":
            s = int_of_string(X.attrib(f, "length"))
            o = offset[0]
            for c in X.children(f):
                gen_read_macro(s, c)
            if s != offset[0] - o:
                raise LengthError(f, s, offset[0] - o)
        else:
            raise Failure("Unexpected field: " + t)

    def gen_send_macro():
        param_name = lambda f: field_name(f).lower()

        def param_names(f, r):
            if X.tag(f) == "field":
                return [param_name(f)] + r
            for c in reversed(X.children(f)):
                r = param_names(c, r)
            return r

        try:
            length = X.attrib(m, "length")
        except X.NoAttribute as e:
            if str(e) != "length":
                raise
            length = "0"
        w("#define XSENS_%s(" % msg_name)
        w(",".join(param_names(m, [])))
        w(") { \\\n")
        w("  XsensHeader(%s, %s);\\\n" % (msg_id, length))

        def send_one_field(f):
            t = X.tag(f)
            if t == "field":
                s = sizeof(fmt_of(f))
                p = param_name(f)
                ty = c_type(fmt_of(f))
                offset[0] += sizeof(fmt_of(f))
                w("  %s _%s = %s; XsensSend%dByAddr((uint8_t*)&_%s);\\\n" % (ty, p, p, s, p))
            elif t == "block":
                for c in X.children(f):
                    send_one_field(c)
            else:
                raise AssertionError("gen_xsens.ml")

        for c in X.children(m):
            send_one_field(c)
        w("  XsensTrailer();\\\n")
        w("}")

    direction = X.attrib(m, "to")
    if direction == "MT":
        gen_send_macro()
    elif direction == "host":
        for c in X.children(m):
            gen_read_macro(0, c)
    else:
        raise Failure("Unexpected direction")
    _check_length(m, offset[0])


def parse_data(d):
    data_name = X.attrib(d, "name")
    w("\n")
    define("XSENS_DATA_%s_LENGTH" % data_name, X.attrib(d, "length"))
    offset = [0]
    for f in X.children(d):
        t = X.tag(f)
        if t != "field":
            raise Failure("Unexpected field: " + t)
        fn = ext_xml.attrib(f, "name")
        fdt = X.attrib(f, "format")
        tt = test_type(X.attrib(f, "format"))
        if tt == "fixe":
            define("XSENS_DATA_%s_%s(_xsens_payload,_xsens_block)" % (data_name, fn), get_at(offset[0], fdt, 1))
            offset[0] += sizeof(fdt)
        elif tt == "variable":
            raise Failure("XSENS_%s_%s: variable data size" % (data_name, fn))
        else:
            offset[0] += sizeof(fdt)
    _check_length(d, offset[0])


def parse_mask(m):
    mask_name = X.attrib(m, "name")
    mask = X.attrib(m, "bitmask")
    shift = ext_xml.attrib_or_default(m, "shift", "")
    w("\n")
    define("XSENS_MASK_%s(_conf)" % mask_name, "((_conf & %s)%s)" % (mask, shift))


def main(argv):
    if len(argv) != 2:
        raise Failure("Usage: %s <.xml xsens protocol file>" % argv[0])
    xml_file = argv[1]
    code = 0
    try:
        xml = X.parse_file(xml_file)
        w("/* Generated by gen_xsens from %s */\n" % xml_file)
        w("/* Please DO NOT EDIT */\n\n")
        define("XSENS_START", "0xFA")
        define("XSENS_BID", "0xFF")
        w("\n")
        for m in X.children(xml):
            t = X.tag(m)
            if t == "message":
                parse_message(m)
            elif t == "data":
                parse_data(m)
            elif t == "mask":
                parse_mask(m)
            else:
                raise Failure("Unexpected tag: %s" % t)
    except LengthError as e:
        sys.stderr.write('File "%s", inconsistent length: %d expected, %d found from fields in message:\n %s\n'
                         % (xml_file, e.l1, e.l2, X.to_string_fmt(e.m)))
        code = 1
    except X.DtdParseError as e:
        sys.stderr.write('File "%s", DTD parse error: %s\n' % (xml_file, e))
    except X.DtdCheckError as e:
        sys.stderr.write('File "%s", DTD check error: %s\n' % (xml_file, e))
    except X.DtdProveError as e:
        sys.stderr.write('\nFile "%s", DTD prove error: %s\n\n' % (xml_file, e))
    sys.stdout.buffer.write("".join(_out).encode("latin-1"))
    sys.stdout.flush()
    if code:
        sys.exit(code)


if __name__ == "__main__":
    try:
        main(sys.argv)
    except Failure as e:
        sys.stdout.buffer.write("".join(_out).encode("latin-1"))
        sys.stderr.write('Fatal error: exception Failure("%s")\n' % e)
        sys.exit(2)
