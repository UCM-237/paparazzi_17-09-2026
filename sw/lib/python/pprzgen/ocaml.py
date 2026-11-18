"""
OCaml standard-library semantics needed for byte-identical output.

The generators were written in OCaml; where Python's built-ins behave
differently (number parsing, float printing, list ordering helpers,
exceptions and exit codes) the OCaml behaviour is reproduced here.
"""

import math
import re


class Failure(Exception):
    """OCaml `Failure` (failwith). Caught at top level: message, exit 1."""


class NotFound(Exception):
    """OCaml `Not_found`."""


class InvalidArgument(Exception):
    """OCaml `Invalid_argument`."""


# --------------------------------------------------------------------------
# Number parsing


_INT_RE = re.compile(r"^([+-]?)(0[xX][0-9a-fA-F_]+|0[oO][0-7_]+|0[bB][01_]+|0[uU][0-9_]+|[0-9][0-9_]*)$")
_MAX_INT = (1 << 62) - 1  # OCaml native int on 64 bit


def int_of_string(s):
    """OCaml int_of_string: sign, 0x/0o/0b/0u prefixes, '_' separators."""
    m = _INT_RE.match(s)
    if not m:
        raise Failure("int_of_string")
    sign, body = m.groups()
    prefix = body[:2].lower()
    if prefix in ("0x", "0o", "0b", "0u"):
        digits = body[2:].replace("_", "")
        if not digits:
            raise Failure("int_of_string")
        base = {"0x": 16, "0o": 8, "0b": 2, "0u": 10}[prefix]
        v = int(digits, base)
        if base != 10 or prefix == "0u":
            # hex/oct/bin literals may use the full unsigned range
            if v > 2 * _MAX_INT + 1:
                raise Failure("int_of_string")
            if v > _MAX_INT and prefix != "0u":
                v -= 2 * (_MAX_INT + 1)
    else:
        v = int(body.replace("_", ""))
        if v > _MAX_INT + (1 if sign == "-" else 0):
            raise Failure("int_of_string")
    return -v if sign == "-" else v


def int_of_string_opt(s):
    try:
        return int_of_string(s)
    except Failure:
        return None


_LEADING_WS = " \t\n\v\f\r"


def float_of_string(s):
    """OCaml float_of_string (strtod on the whole string, '_' ignored)."""
    t = s.replace("_", "")
    u = t.lstrip(_LEADING_WS)
    if not u or u != u.rstrip(_LEADING_WS):
        raise Failure("float_of_string")
    body = u[1:] if u[0] in "+-" else u
    neg = u[0] == "-"
    low = body.lower()
    try:
        if low.startswith("0x"):
            v = float.fromhex(("-" if neg else "") + body)
        elif low in ("inf", "infinity"):
            v = -math.inf if neg else math.inf
        elif low == "nan":
            v = math.nan
        else:
            if not re.match(r"^([0-9]+\.?[0-9]*|\.[0-9]+)([eE][+-]?[0-9]+)?$", body):
                raise ValueError
            v = float(u)
    except ValueError:
        raise Failure("float_of_string")
    return v


def float_of_string_opt(s):
    try:
        return float_of_string(s)
    except Failure:
        return None


# --------------------------------------------------------------------------
# Number printing


def string_of_float(f):
    """OCaml string_of_float: "%.12g" made a valid float lexeme."""
    s = format_float("%.12g", f)
    if all(c in "0123456789-" for c in s):
        return s + "."
    return s


def format_float(fmt, f):
    """C printf-style float formatting (OCaml Printf), including inf/nan."""
    if math.isnan(f):
        return "-nan" if math.copysign(1.0, f) < 0 else "nan"
    return fmt % f


def string_of_int(i):
    return str(i)


def sprintf_float(fmt, f):
    """Printf.sprintf with a single float conversion like "%.2f"."""
    if math.isinf(f):
        return fmt % f  # Python prints inf/-inf like glibc
    return format_float(fmt, f)


# --------------------------------------------------------------------------
# Lists


def ocaml_compare(a, b):
    """Polymorphic compare restricted to the types we use (str/int/float/tuples)."""
    if a == b:
        return 0
    return -1 if a < b else 1


def list_sort_unique(lst, key=None):
    """Gen_common.singletonize: sort, then drop consecutive duplicates,
    keeping the LAST element of each run of equal elements."""
    if key is None:
        key = lambda x: x
    s = sorted(lst, key=key)  # List.sort is stable like Python's sort
    out = []
    for i, x in enumerate(s):
        if i + 1 < len(s) and key(s[i + 1]) == key(x):
            continue
        out.append(x)
    return out


# --------------------------------------------------------------------------
# Hashtbl (iteration order of OCaml 4.14 hash tables, non-randomized)

_M32 = 0xFFFFFFFF


def _rotl32(x, n):
    return ((x << n) | (x >> (32 - n))) & _M32


def _mix(h, d):
    d = (d * 0xCC9E2D51) & _M32
    d = _rotl32(d, 15)
    d = (d * 0x1B873593) & _M32
    h ^= d
    h = _rotl32(h, 13)
    return (h * 5 + 0xE6546B64) & _M32


def _final_mix(h):
    h ^= h >> 16
    h = (h * 0x85EBCA6B) & _M32
    h ^= h >> 13
    h = (h * 0xC2B2AE35) & _M32
    h ^= h >> 16
    return h


def hash_string(s, seed=0):
    """caml_hash(10, 100, seed, s) for a string (Latin-1 bytes)."""
    b = s.encode("latin-1") if isinstance(s, str) else bytes(s)
    n = len(b)
    h = seed & _M32
    i = 0
    while i + 4 <= n:
        w = b[i] | (b[i + 1] << 8) | (b[i + 2] << 16) | (b[i + 3] << 24)
        h = _mix(h, w)
        i += 4
    r = n & 3
    if r:
        w = 0
        if r == 3:
            w = b[i + 2] << 16
        if r >= 2:
            w |= b[i + 1] << 8
        w |= b[i]
        h = _mix(h, w)
    h ^= n & _M32
    return _final_mix(h) & 0x3FFFFFFF


class Hashtbl:
    """OCaml Hashtbl with string keys, reproducing its iteration order."""

    def __init__(self, initial_size=16, keymodel=None):
        """keymodel: function giving the OCaml value model of a key (see
        caml_hash); default: keys are strings."""
        s = 16
        while s < initial_size:
            s *= 2
        self.data = [[] for _ in range(s)]  # each bucket: list, index 0 = head
        self.size = 0
        self.keymodel = keymodel

    def _index(self, key):
        h = hash_string(key) if self.keymodel is None else caml_hash(self.keymodel(key))
        return h & (len(self.data) - 1)

    def _resize(self):
        odata = self.data
        ndata = [[] for _ in range(len(odata) * 2)]
        self.data = ndata
        for bucket in odata:
            for k, v in bucket:
                ndata[self._index(k)].append((k, v))

    def add(self, key, data):
        i = self._index(key)
        self.data[i].insert(0, (key, data))
        self.size += 1
        if self.size > len(self.data) * 2:
            self._resize()

    def replace(self, key, data):
        i = self._index(key)
        b = self.data[i]
        for j, (k, _) in enumerate(b):
            if k == key:
                b[j] = (key, data)
                return
        b.insert(0, (key, data))
        self.size += 1
        if self.size > len(self.data) * 2:
            self._resize()

    def find(self, key):
        for k, v in self.data[self._index(key)]:
            if k == key:
                return v
        raise NotFound(key)

    def find_all(self, key):
        return [v for k, v in self.data[self._index(key)] if k == key]

    def mem(self, key):
        return any(k == key for k, _ in self.data[self._index(key)])

    def remove(self, key):
        b = self.data[self._index(key)]
        for j, (k, _) in enumerate(b):
            if k == key:
                del b[j]
                self.size -= 1
                return

    def length(self):
        return self.size

    def items(self):
        """Bindings in Hashtbl.iter / Hashtbl.fold order."""
        for bucket in self.data:
            for k, v in bucket:
                yield k, v

    def fold(self, f, init):
        acc = init
        for k, v in self.items():
            acc = f(k, v, acc)
        return acc


# --------------------------------------------------------------------------
# Float arithmetic corner cases

MIN_INT = -(1 << 62)
MAX_INT = (1 << 62) - 1


def fdiv(a, b):
    """IEEE float division as in OCaml (no exception on division by zero)."""
    try:
        return a / b
    except ZeroDivisionError:
        if a == 0 or math.isnan(a):
            return math.nan
        return math.copysign(math.inf, a) * math.copysign(1.0, b)


def truncate(x):
    """OCaml `truncate` (bytecode, x86-64): C cast to int64 then tagged to
    63 bits. inf/nan/out of range give INT64_MIN, which tags to 0."""
    if math.isnan(x) or math.isinf(x) or abs(x) >= 2.0 ** 63:
        return 0
    n = int(x)
    n &= (1 << 64) - 1
    n = (n << 1) & ((1 << 64) - 1)  # tagging shift drops the top bit
    if n >= 1 << 63:
        n -= 1 << 64
    return n >> 1


def fmod(a, b):
    return math.fmod(a, b) if b != 0 and not math.isinf(a) else math.nan


# Generic caml_hash over a model of OCaml values:
#   int -> immediate, str -> string block, OBlock(tag, fields) -> block.
class OBlock:
    __slots__ = ("tag", "fields")

    def __init__(self, tag, fields):
        self.tag = tag
        self.fields = list(fields)


def caml_hash(obj, count=10, limit=100, seed=0):
    queue = [obj]
    rd = 0
    num = count
    h = seed & _M32
    while rd < len(queue) and num > 0:
        v = queue[rd]
        rd += 1
        if isinstance(v, bool):
            v = int(v)
        if isinstance(v, int):
            d = (2 * v + 1) & ((1 << 64) - 1)  # tagged representation
            if d >= 1 << 63:
                d -= 1 << 64
            n = ((d >> 32) ^ (d >> 63) ^ d) & _M32
            h = _mix(h, n)
            num -= 1
        elif isinstance(v, str):
            h = _mix_string(h, v)
            num -= 1
        else:
            hd = (len(v.fields) << 10) | v.tag
            h = _mix(h, hd & _M32)
            for f in v.fields:
                if len(queue) >= limit:
                    break
                queue.append(f)
    return _final_mix(h) & 0x3FFFFFFF


def _mix_string(h, s):
    b = s.encode("latin-1")
    n = len(b)
    i = 0
    while i + 4 <= n:
        h = _mix(h, b[i] | (b[i + 1] << 8) | (b[i + 2] << 16) | (b[i + 3] << 24))
        i += 4
    r = n & 3
    if r:
        w = 0
        if r == 3:
            w = b[i + 2] << 16
        if r >= 2:
            w |= b[i + 1] << 8
        w |= b[i]
        h = _mix(h, w)
    return h ^ (n & _M32)


def option_key(x):
    """OCaml value model of a `string option` (None -> 0, Some s -> block)."""
    return 0 if x is None else OBlock(0, [x])


# --------------------------------------------------------------------------
# C math library semantics (no Python exceptions: nan / inf like libm)

class cm:
    """Math functions returning nan/inf like the C library instead of
    raising ValueError/OverflowError (OCaml calls libm directly)."""

    @staticmethod
    def _wrap(f, *a):
        try:
            return f(*a)
        except ValueError:
            return math.nan
        except OverflowError:
            return math.inf

    @staticmethod
    def pow(a, b):
        try:
            r = math.pow(a, b)
        except ValueError:
            return math.nan
        except OverflowError:
            # sign: negative base with odd integer exponent
            if a < 0 and b == int(b) and int(b) % 2 == 1:
                return -math.inf
            return math.inf
        return r

    @staticmethod
    def log(x):
        if x == 0:
            return -math.inf
        return cm._wrap(math.log, x)

    @staticmethod
    def sqrt(x):
        return cm._wrap(math.sqrt, x)

    @staticmethod
    def exp(x):
        return cm._wrap(math.exp, x)

    @staticmethod
    def sinh(x):
        try:
            return math.sinh(x)
        except OverflowError:
            return math.copysign(math.inf, x)

    @staticmethod
    def cosh(x):
        return cm._wrap(math.cosh, x)

    @staticmethod
    def asin(x):
        return cm._wrap(math.asin, x)

    @staticmethod
    def tan(x):
        return cm._wrap(math.tan, x)

    @staticmethod
    def sin(x):
        return cm._wrap(math.sin, x)

    @staticmethod
    def cos(x):
        return cm._wrap(math.cos, x)

    @staticmethod
    def atan(x):
        return math.atan(x)

    @staticmethod
    def atan2(y, x):
        return math.atan2(y, x)

    @staticmethod
    def floor(x):
        if math.isnan(x) or math.isinf(x):
            return x
        return float(math.floor(x))


def concat(*parts):
    """Filename.concat (folded): unlike os.path.join, an absolute second
    component does NOT discard the first one."""
    res = parts[0]
    for p in parts[1:]:
        res = res + p if (res == "" or res.endswith("/")) else res + "/" + p
    return res
