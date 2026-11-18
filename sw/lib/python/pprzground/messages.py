"""PprzLink v2 messages with the exact semantics of the OCaml PprzLink
library used by the historical ground segment (link, server):

- binary payload <-> values (header: sender, receiver, class/component, id);
- Ivy string <-> values, with the same number formatting (field "format"
  attribute or per-type default, C printf semantics), so that PprzGCS, the
  log files and every Ivy client see exactly the same text as before.

Values are tuples (kind, v): ("int", int) ("float", float) ("int32", int)
("int64", int) ("char", str) ("string", str) ("array", [values]).
"""

import math
import os
import re
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if os.path.join(_HERE, "..") not in sys.path:
    sys.path.insert(0, os.path.join(_HERE, ".."))

from pprzgen import xml_light as X  # noqa: E402
from pprzgen.ocaml import Failure, NotFound, float_of_string, int_of_string as _ocaml_int, string_of_float  # noqa: E402

BROADCAST_ID = 0xFF
SEPARATOR = ","

OFFSET_SENDER_ID, OFFSET_RECEIVER_ID, OFFSET_CLASS_ID, OFFSET_MSG_ID, OFFSET_FIELDS = 0, 1, 2, 3, 4

# type -> (default format, size)
TYPES = {
    "uint8": ("%u", 1), "uint16": ("%u", 2), "uint32": ("%Lu", 4), "uint64": ("%Lu", 8),
    "int8": ("%d", 1), "int16": ("%d", 2), "int32": ("%ld", 4), "int64": ("%Ld", 8),
    "float": ("%f", 4), "double": ("%f", 8), "char": ("%c", 1), "string": ("%s", None),
}
DEFAULT_VALUES = {"uint8": ("int", 42), "uint16": ("int", 42), "uint32": ("int", 42), "uint64": ("int", 42),
                  "int8": ("int", 42), "int16": ("int", 42), "int32": ("int", 42), "int64": ("int", 42),
                  "float": ("float", 4.2), "double": ("float", 4.2), "char": ("char", "*"),
                  "string": ("string", "42")}


def pprzlink_dir():
    d = os.environ.get("PPRZLINK_DIR")
    if d is not None:
        return d
    h = os.environ.get("PAPARAZZI_HOME")
    if h is not None:
        return os.path.join(h, "var")
    return "/usr/share/pprzlink"


# --------------------------------------------------------------------------
# Field types

class FieldType:
    """("scalar", t) | ("array", t) | ("fixed", t, n)"""


def parse_type(t):
    if len(t) >= 2 and t.endswith("[]"):
        return ("array", t[:-2])
    m = re.fullmatch(r"([^\[\]]+)\[([^\[\]]+)\]", t)
    if m:
        try:
            n = _ocaml_int(m.group(2))
        except Failure:
            raise Failure("PprzLink.type_of_fixed_array_type: length is not an integer")
        return ("fixed", m.group(1), n)
    return ("scalar", t)


def sizeof_scalar(t):
    if t not in TYPES:
        raise NotFound(t)
    return TYPES[t][1]


def default_format(ft):
    t = ft[1]
    if t not in TYPES:
        raise Failure("Unknown format '%s'" % t)
    return TYPES[t][0]


def default_value(ft):
    if ft[0] != "scalar":
        raise Failure("default_value: Array")
    return DEFAULT_VALUES[ft[1]]


class Field:
    __slots__ = ("name", "type", "format", "values")

    def __init__(self, name, ftype, fmt, values):
        self.name, self.type, self.format, self.values = name, ftype, fmt, values


class Message:
    __slots__ = ("name", "id", "fields", "link")

    def __init__(self, name, id_, fields, link):
        self.name, self.id, self.fields, self.link = name, id_, fields, link


# --------------------------------------------------------------------------
# printf with OCaml/C semantics

_FMT_RE = re.compile(r"%([-+ #0]*)(\d+)?(?:\.(\d+))?([lLn]?)([diuxXoeEfFgGcs])")


def _c_format(fmt, kind, v):
    """sprintf (format_from_string fmt default) v for one conversion."""
    m = _FMT_RE.search(fmt)
    if m is None:
        return fmt
    flags, width, prec, _len, conv = m.groups()
    spec = "%" + flags + (width or "") + ("." + prec if prec is not None else "")
    if conv in "diu":
        if conv == "u" and v < 0:
            v &= (1 << 64) - 1 if kind == "int64" else (1 << 63) - 1
        s = (spec + "d") % v
    elif conv in "xXo":
        if v < 0:
            v &= (1 << 64) - 1 if kind in ("int64",) else ((1 << 32) - 1 if kind == "int32" else (1 << 63) - 1)
        s = (spec + conv) % v
    elif conv in "eEfFgG":
        if math.isnan(v):
            s = ("-nan" if math.copysign(1.0, v) < 0 else "nan")
            if width:
                s = s.rjust(int(width)) if "-" not in flags else s.ljust(int(width))
        else:
            s = (spec + conv) % v
    elif conv == "c":
        s = (spec + "s") % v
    else:
        s = (spec + "s") % v
    return fmt[:m.start()] + s + fmt[m.end():]


def formatted_string_of_value(fmt, v):
    k = v[0]
    if k == "string":
        x = v[1]
        return '"%s"' % x if " " in x else x
    if k == "array":
        l = [formatted_string_of_value(fmt, e) for e in v[1]]
        if not v[1]:
            # OCaml looks at a.(0): empty arrays raise Invalid_argument, so the
            # whole message is dropped by the link/server
            raise IndexError("index out of bounds")
        if v[1][0][0] == "char":
            return '"' + "".join(l) + '"'
        return SEPARATOR.join(l)
    if k == "float":
        return _c_format(fmt, k, v[1])
    if k == "char":
        return _c_format(fmt, k, v[1])
    return _c_format(fmt, k, v[1])


def string_of_value(v):
    k = v[0]
    if k in ("int", "int32", "int64"):
        return str(v[1])
    if k == "float":
        return string_of_float(v[1])
    if k in ("char", "string"):
        return v[1]
    l = [string_of_value(e) for e in v[1]]
    if not v[1]:
        raise IndexError("index out of bounds")
    if v[1][0][0] == "char":
        return '"' + "".join(l) + '"'
    return SEPARATOR.join(l)


def int_of_value(v):
    if v[0] in ("int", "int32", "int64"):
        return v[1]
    raise Failure("PprzLink.int_assoc")


# --------------------------------------------------------------------------
# value parsing from strings (Ivy)

def _int_of_string(x):
    try:
        return _ocaml_int(x)
    except Failure:
        try:
            return _ocaml_int("0x" + x)
        except Failure:
            raise Failure("PprzLink.int_of_string: %s" % x)


def _int32_of_string(x):
    v = _ocaml_int(x)
    if not -(1 << 31) <= v <= (1 << 32) - 1:
        raise Failure("Int32.of_string")
    return v - (1 << 32) if v >= (1 << 31) else v


def _int64_of_string(x):
    v = _ocaml_int(x)
    return v


def value_of_string(ft, v):
    if ft[0] == "scalar":
        t = ft[1]
        if t in ("uint8", "uint16", "int8", "int16"):
            return ("int", _int_of_string(v))
        if t == "int32":
            return ("int32", _int32_of_string(v))
        if t in ("uint32", "uint64", "int64"):
            return ("int64", _int64_of_string(v))
        if t in ("float", "double"):
            return ("float", float_of_string(v))
        if t == "string":
            return ("string", v)
        if t == "char":
            if v == "":
                raise IndexError("index out of bounds")
            return ("char", v[0])
        raise Failure("PprzLink.value: Unexpected type: %s" % t)
    t = ft[1]
    if t == "char":
        return ("string", v)
    parts = [p for p in v.split(",") if p != ""]
    return ("array", [value_of_string(("scalar", t), p) for p in parts])


# --------------------------------------------------------------------------
# binary

def _value_of_bin(buf, i, ft):
    kind = ft[0]
    if kind == "scalar":
        t = ft[1]
        if t == "uint8":
            return ("int", buf[i]), 1
        if t == "char":
            return ("char", chr(buf[i])), 1
        if t == "int8":
            return ("int", struct.unpack_from("<b", buf, i)[0]), 1
        if t == "uint16":
            return ("int", struct.unpack_from("<H", buf, i)[0]), 2
        if t == "int16":
            return ("int", struct.unpack_from("<h", buf, i)[0]), 2
        if t == "float":
            return ("float", struct.unpack_from("<f", buf, i)[0]), 4
        if t == "double":
            return ("float", struct.unpack_from("<d", buf, i)[0]), 8
        if t == "int32":
            return ("int32", struct.unpack_from("<i", buf, i)[0]), 4
        if t == "uint32":
            return ("int64", struct.unpack_from("<I", buf, i)[0]), 4
        if t in ("int64", "uint64"):
            return ("int64", struct.unpack_from("<q", buf, i)[0]), 8
        if t == "string":
            n = buf[i]
            if i + 1 + n > len(buf):
                raise IndexError("index out of bounds")
            return ("string", bytes(buf[i + 1:i + 1 + n]).decode("latin-1")), 1 + n
        raise Failure("value_of_bin")
    t = ft[1]
    if kind == "array":
        n = buf[i]
        s = sizeof_scalar(t)
        return ("array", [_value_of_bin(buf, i + 1 + k * s, ("scalar", t))[0] for k in range(n)]), 1 + n * s
    n = ft[2]
    s = sizeof_scalar(t)
    return ("array", [_value_of_bin(buf, i + k * s, ("scalar", t))[0] for k in range(n)]), n * s


def _f32(x):
    """C (float) cast: round to nearest, overflow to inf."""
    try:
        return struct.pack("<f", x)
    except OverflowError:
        return struct.pack("<f", math.copysign(math.inf, x))


def _sprint_value(ft, v):
    kind = ft[0]
    if kind == "scalar":
        t, vk, x = ft[1], v[0], v[1]
        if t == "uint8" and vk == "int":
            if x < 0 or x > 0xFF:
                raise Failure("Value too large to fit in a uint8: %d" % x)
            return bytes([x])
        if t == "int8" and vk == "int":
            if x < -0x7F or x > 0x7F:
                raise Failure("Value too large to fit in a int8: %d" % x)
            return bytes([x & 0xFF])
        if t == "float" and vk == "float":
            return _f32(x)
        if t == "double" and vk == "float":
            return struct.pack("<d", x)
        if t == "int32" and vk == "int32":
            return struct.pack("<I", x & 0xFFFFFFFF)
        if t == "uint32" and vk == "int64":
            # OCaml writes 8 bytes but advances by 4: the low 4 bytes remain
            return struct.pack("<I", x & 0xFFFFFFFF)
        if t in ("int64", "uint64") and vk == "int64":
            return struct.pack("<Q", x & 0xFFFFFFFFFFFFFFFF)
        if t == "int16" and vk == "int":
            return struct.pack("<H", x & 0xFFFF)
        if t in ("int32", "uint32") and vk == "int":
            return struct.pack("<I", x & 0xFFFFFFFF)
        if t in ("int64", "uint64") and vk == "int":
            return struct.pack("<Q", x & 0xFFFFFFFFFFFFFFFF)
        if t == "uint16" and vk == "int":
            return struct.pack("<H", x & 0xFFFF)
        if t == "string" and vk == "string":
            b = x.encode("latin-1")
            return bytes([len(b)]) + b
        if t == "char" and vk == "char":
            return x.encode("latin-1")[:1]
        raise Failure("PprzLink.sprint_value (%s):%s" % (t, string_of_value(v)))
    t = ft[1]
    if v[0] == "string" and t == "char":
        v = ("array", [("char", c) for c in v[1]])
    if v[0] != "array":
        raise Failure("PprzLink.sprint_value (%s)" % t)
    elems = v[1]
    if kind == "array":
        out = _sprint_value(("scalar", "uint8"), ("int", len(elems)))
        return out + b"".join(_sprint_value(("scalar", t), e) for e in elems)
    n = ft[2]
    s = sizeof_scalar(t)
    out = b"".join(_sprint_value(("scalar", t), e) for e in elems[:n])
    return out + b"\x00" * (n * s - len(out))


# --------------------------------------------------------------------------
# message classes

def parse_messages_file(path=None):
    path = path or os.path.join(pprzlink_dir(), "messages.xml")
    return X.parse_file(path)


class MessageClass:
    max_length = 256

    def __init__(self, name, xml=None):
        xml = xml or _messages_xml()
        cls = None
        for c in X.children(xml):
            if isinstance(c, X.Element) and c.tag in ("msg_class", "class") and X.attrib(c, "name") == name:
                cls = c
                break
        if cls is None:
            raise Failure("Unknown message class: %s" % name)
        self.name = name
        try:
            self.class_id = _ocaml_int(X.attrib(cls, "id"))
        except Exception:
            self.class_id = None
        self.by_id = {}
        self.by_name = {}
        for m in X.children(cls):
            mname = X.attrib(m, "name")
            try:
                link = X.attrib(m, "link")
            except X.NoAttribute:
                link = None
            fields = []
            for f in X.children(m):
                if not (isinstance(f, X.Element) and f.tag == "field"):
                    continue
                ft = parse_type(X.attrib(f, "type"))
                try:
                    fmt = X.attrib(f, "format")
                except X.NoAttribute:
                    fmt = default_format(ft)
                try:
                    vals = [v for v in X.attrib(f, "values").split("|") if v != ""]
                except X.NoAttribute:
                    vals = []
                fields.append(Field(X.attrib(f, "name").lower(), ft, fmt, vals))
            mid = _ocaml_int(X.attrib(m, "id"))
            if mid in self.by_id:
                raise Failure("Duplicated id in messages.xml: %d" % mid)
            msg = Message(mname, mid, fields, link)
            self.by_id[mid] = msg
            self.by_name[mname] = msg

    def message_of_id(self, i):
        if i not in self.by_id:
            sys.stderr.write("message_of_id :%d\n" % i)
            raise NotFound(i)
        return self.by_id[i]

    def message_of_name(self, name):
        if name not in self.by_name:
            raise KeyError("Unknown_msg_name(%s, %s)" % (name, self.name))
        return self.by_name[name]

    def values_of_payload(self, buf):
        """returns (header dict, [(field, value)])"""
        try:
            mid = buf[OFFSET_MSG_ID]
            header = {"sender_id": buf[OFFSET_SENDER_ID], "receiver_id": buf[OFFSET_RECEIVER_ID],
                      "class_id": buf[OFFSET_CLASS_ID] & 0x0F,
                      "component_id": (buf[OFFSET_CLASS_ID] & 0xF0) >> 4, "message_id": mid}
            if self.class_id is not None and header["class_id"] != self.class_id:
                raise Failure("PprzLink.invalid class ID %d" % header["class_id"])
            msg = self.message_of_id(mid)
            values = []
            i = OFFSET_FIELDS
            for f in msg.fields:
                v, n = _value_of_bin(buf, i, f.type)
                values.append((f.name, v))
                i += n
            if i != len(buf):
                raise Failure("PprzLink.values_of_payload, too many bytes in message %s" % msg.name)
            return header, values
        except (IndexError, struct.error):
            raise Failure("PprzLink.values_of_payload, wrong argument")

    def payload_of_values(self, mid, sender_id, receiver_id, values):
        msg = self.message_of_id(mid)
        vd = dict(values)
        out = bytearray([sender_id & 0xFF, receiver_id & 0xFF,
                         (self.class_id or 0) & 0xFF, mid & 0xFF])
        for f in msg.fields:
            v = vd.get(f.name)
            if v is None:
                v = default_value(f.type)
            out += _sprint_value(f.type, v)
        if len(out) > self.max_length:
            raise Failure("Error in sprint_value: message too long")
        return bytes(out)

    def values_of_string(self, s):
        """Ivy text 'MSG_NAME v1 v2 ...' -> (msg, values)"""
        args = _split_ivy(s)
        if not args:
            raise ValueError("PprzLink.values_of_string: %s" % s)
        msg = self.message_of_name(args[0])
        rest = args[1:]
        if len(rest) != len(msg.fields):
            raise Failure("PprzLink.values_of_string: incorrect number of fields in '%s'" % s)
        return msg, [(f.name, value_of_string(f.type, a)) for f, a in zip(msg.fields, rest)]

    def string_of_message(self, msg, values, sep=" "):
        vd = {}
        for k, v in values:
            if not any(f.name == k for f in msg.fields):
                raise ValueError("PprzLink.string_of_message: unknown field '%s' in message '%s'" % (k, msg.name))
            vd.setdefault(k, v)
        parts = [msg.name]
        for f in msg.fields:
            v = vd.get(f.name)
            if v is None:
                v = default_value(f.type)
            parts.append(formatted_string_of_value(f.format, v))
        return sep.join(parts)


def _split_ivy(s):
    """values_of_string splitting: quoted ("..." or |...|) arrays/strings."""
    toks = re.split(r'(["|])', s)
    toks = [t for t in toks if t != ""]
    out = []
    i = 0
    while i < len(toks):
        t = toks[i]
        if t in ('"', "|"):
            if i + 2 < len(toks) and toks[i + 2] == t and toks[i + 1] not in ('"', "|"):
                out.append(toks[i + 1])
                i += 3
                continue
            raise Failure("PprzLink.values_of_string: incorrect array delimiter")
        out += [x for x in re.split(r"[ \t]+", t) if x != ""]
        i += 1
    return out


_xml_cache = None


def _messages_xml():
    global _xml_cache
    if _xml_cache is None:
        _xml_cache = parse_messages_file()
    return _xml_cache


# --------------------------------------------------------------------------
# helpers used by the server

def string_of_values(values):
    """Server_globals.string_of_values (log lines): plain string_of_value."""
    return " ".join(string_of_value(v) for _, v in values)


def json_of_message(msg, values):
    vd = {}
    for k, v in values:
        if not any(f.name == k for f in msg.fields):
            raise ValueError("json_of_message: unknown field '%s' in message '%s'" % (k, msg.name))
        vd.setdefault(k, v)
    parts = []
    for f in msg.fields:
        if f.name not in vd:
            raise Failure("json_of_message: field_name not found")
        v = vd[f.name]
        if v[0] == "array":
            parts.append('"%s": { %s }' % (f.name, ", ".join(
                '"%s_%i": %s' % (f.name, i, formatted_string_of_value(f.format, e)) for i, e in enumerate(v[1]))))
        elif v[0] == "string":
            parts.append('"%s": "%s"' % (f.name, v[1]))
        else:
            parts.append('"%s": %s' % (f.name, formatted_string_of_value(f.format, v)))
    return '{ "%s": { %s } }' % (msg.name, ", ".join(parts))


def assoc(a, values):
    a = a.lower()
    for k, v in values:
        if k == a:
            return v
    raise Failure("Attribute '%s' not found in '%s'" % (a, " ".join("%s=%s" % (k, string_of_value(v)) for k, v in values)))


def string_assoc(a, values):
    return string_of_value(assoc(a, values))


def int_assoc(a, values):
    return int_of_value(assoc(a, values))


def float_assoc(a, values):
    v = assoc(a, values)
    if v[0] != "float":
        raise ValueError("PprzLink.float_assoc")
    return v[1]
