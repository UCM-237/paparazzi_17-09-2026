#!/usr/bin/env python3
"""UBX protocol header generator (Python replacement of gen_ubx.out).

Usage: gen_ubx.py <ubx.xml>   (header written on stdout)
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "lib", "python"))

from pprzgen import xml_light as X, ext_xml  # noqa: E402
from pprzgen.ocaml import Failure, int_of_string  # noqa: E402

_out = []


def w(s):
    _out.append(s)


SIZES = {"R8": 8, "U4": 4, "I4": 4, "R4": 4, "U2": 2, "I2": 2, "U1": 1, "I1": 1}
CTYPES = {"R8": "double", "R4": "float", "I4": "int32_t", "U4": "uint32_t", "I2": "int16_t",
          "U2": "uint16_t", "I1": "int8_t", "U1": "uint8_t"}


def sizeof(f):
    if f not in SIZES:
        raise Failure("sizeof: unknown format '%s'" % f)
    return SIZES[f]


def c_type(f):
    if f not in CTYPES:
        raise Failure("Gen_ubx.c_type: unknown format '%s'" % f)
    return CTYPES[f]


def get_at(offset, fmt, block_size):
    t = c_type(fmt)
    bo = "" if block_size == 0 else "+%d*_ubx_block" % block_size
    if fmt == "R8":
        s = "*((uint8_t*)_ubx_payload+%d%s)" % (offset, bo)
        for i in range(1, 8):
            s += "|((uint64_t)*((uint8_t*)_ubx_payload+%d+%d%s))<<%d" % (i, offset, bo, 8 * i)
        return "({ union { uint64_t u; double f; } _f; _f.u = (uint64_t)(%s); /*Swap32IfBigEndian(_f.u)*/; _f.f; })" % s
    if fmt == "R4":
        return ("({ union { uint32_t u; float f; } _f; _f.u = (uint32_t)(*((uint8_t*)_ubx_payload+%d%s)|(uint32_t)(*((uint8_t*)_ubx_payload+1+%d%s))<<8|((uint32_t)*((uint8_t*)_ubx_payload+2+%d%s))<<16|((uint32_t)*((uint8_t*)_ubx_payload+3+%d%s))<<24); _f.f; })"
                % (offset, bo, offset, bo, offset, bo, offset, bo))
    if fmt in ("U4", "I4"):
        return ("(%s)(*((uint8_t*)_ubx_payload+%d%s)|(%s)(*((uint8_t*)_ubx_payload+1+%d%s))<<8|((%s)*((uint8_t*)_ubx_payload+2+%d%s))<<16|((%s)*((uint8_t*)_ubx_payload+3+%d%s))<<24)"
                % (t, offset, bo, t, offset, bo, t, offset, bo, t, offset, bo))
    if fmt in ("U2", "I2"):
        return "(%s)(*((uint8_t*)_ubx_payload+%d%s)|(%s)(*((uint8_t*)_ubx_payload+1+%d%s))<<8)" % (t, offset, bo, t, offset, bo)
    if fmt in ("U1", "I1"):
        return "(%s)(*((uint8_t*)_ubx_payload+%d%s))" % (t, offset, bo)
    raise Failure("Gen_ubx.c_type: unknown format '%s'" % fmt)


def define(x, y):
    w("#define %s %s\n" % (x, y))


class LengthError(Exception):
    def __init__(self, m, l1, l2):
        super().__init__(l1, l2)
        self.m, self.l1, self.l2 = m, l1, l2


def parse_message(class_name, m):
    msg_name = X.attrib(m, "name")
    w("\n")
    msg_id = "UBX_%s_%s_ID" % (class_name, msg_name)
    define(msg_id, X.attrib(m, "ID"))
    field_name = lambda f: ext_xml.attrib(f, "name")
    fmt_of = lambda f: X.attrib(f, "format")
    offset = [0]

    def gen_access_macro(block_size, f):
        t = X.tag(f)
        if t == "field":
            fn = field_name(f)
            fmt = fmt_of(f)
            block_no = "" if block_size == 0 else ",_ubx_block"
            define("UBX_%s_%s_%s(_ubx_payload%s)" % (class_name, msg_name, fn, block_no), get_at(offset[0], fmt, block_size))
            offset[0] += sizeof(fmt)
        elif t == "block":
            s = int_of_string(X.attrib(f, "length"))
            o = offset[0]
            for c in X.children(f):
                gen_access_macro(s, c)
            s2 = offset[0] - o
            if s != s2:
                raise LengthError(f, s, s2)
        else:
            raise Failure("Unexpected field: " + t)

    for c in X.children(m):
        gen_access_macro(0, c)
    try:
        l = int_of_string(X.attrib(m, "length"))
        if l != offset[0]:
            raise LengthError(m, l, offset[0])
    except X.NoAttribute as e:
        if str(e) != "length":
            raise

    param_type = lambda f: c_type(fmt_of(f))
    param_name = lambda f: field_name(f).lower()

    def param_names(f, r):
        if X.tag(f) == "field":
            return ["%s ubx_%s" % (param_type(f), param_name(f))] + r
        for c in reversed(X.children(f)):
            r = param_names(c, r)
        return r

    w("\nstatic inline void UbxSend_%s_%s(" % (class_name, msg_name))
    w(", ".join(["struct link_device *dev"] + param_names(m, [])))
    w(") {\n")
    w("  ubx_header(dev, UBX_%s_ID, %s, %d);\n" % (class_name, msg_id, offset[0]))

    def send_one_field(f):
        t = X.tag(f)
        if t == "field":
            s = sizeof(fmt_of(f))
            p = param_name(f)
            ty = param_type(f)
            w("  %s _%s = ubx_%s; ubx_send_bytes(dev, %d, (uint8_t*)&_%s);\n" % (ty, p, p, s, p))
        elif t == "block":
            for c in X.children(f):
                send_one_field(c)
        else:
            raise AssertionError("gen_ubx.ml")

    for c in X.children(m):
        send_one_field(c)
    w("  ubx_trailer(dev);\n")
    w("}\n")


def parse_class(c):
    int_of_string(X.attrib(c, "id"))
    class_name = X.attrib(c, "name")
    w("\n")
    define("UBX_%s_ID" % class_name, X.attrib(c, "ID"))
    for m in X.children(c):
        parse_message(class_name, m)


def main(argv):
    if len(argv) != 2:
        raise Failure("Usage: %s <.xml ubx protocol file>" % argv[0])
    xml_file = argv[1]
    code = 0
    try:
        xml = X.parse_file(xml_file)
        w("/* Generated by gen_ubx from %s */\n" % xml_file)
        w("/* Please DO NOT EDIT */\n\n")
        w('#include "pprzlink/pprzlink_device.h"\n\n')
        w('#include "modules/gps/gps_ubx.h"\n\n')
        define("UBX_SYNC1", "0xB5")
        define("UBX_SYNC2", "0x62")
        for c in X.children(xml):
            parse_class(c)
    except LengthError as e:
        sys.stderr.write('File "%s", inconsistent length: %d expected, %d found from fields in message:\n %s\n'
                         % (xml_file, e.l1, e.l2, X.to_string_fmt(e.m)))
        code = 1
    except X.DtdCheckError as e:
        sys.stderr.write('File "%s", DTD check error: %s\n' % (xml_file, e))
    except X.DtdProveError as e:
        sys.stderr.write('\nFile "%s", DTD check error: %s\n\n' % (xml_file, e))
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
