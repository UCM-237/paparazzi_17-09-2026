"""Port of sw/lib/ocaml/expr_lexer.mll, expr_parser.mly and expr_syntax.ml:
C-like expressions used in flight plans and module conditions.

Expressions are tuples:
  ("ident", s) ("int", n) ("float", f) ("call", name, [args])
  ("op", op, [args]) ("index", name, e) ("field", name, field) ("deref", e, field)
"""

import re
import sys

from .ocaml import Failure, int_of_string, float_of_string


class LexError(Exception):
    def __init__(self, pos):
        super().__init__(pos)
        self.pos = pos


class ParseError(Exception):
    def __init__(self, lexeme):
        super().__init__(lexeme)
        self.lexeme = lexeme


# token rules in ocamllex priority order: (regex, kind); longest match wins,
# earlier rule wins on ties
_RULES = [
    (re.compile(r"[ \t\n]"), "SKIP"),
    (re.compile(r"/\*(?:[^*]|\*[^/])*\**/", re.S), "SKIP"),
    (re.compile(r"[0-9]+"), "INT"),
    (re.compile(r"[0-9]+\.[0-9]*"), "FLOAT"),
    (re.compile(r"\$?[a-z_A-Z][a-zA-Z_0-9]*"), "IDENT"),
    (re.compile(r"'[^']+'", re.S), "QIDENT"),
]
_FIXED = [
    (",", "COMMA"), (".", "DOT"), (";", "SEMICOLON"), (":", "COLON"), ("(", "LP"), (")", "RP"),
    ("{", "LC"), ("}", "RC"), ("[", "LB"), ("]", "RB"), ("->", "DEREF"), ("@DEREF", "DEREF"),
    ("==", "EQ"), ("&&", "AND"), ("@AND", "AND"), ("||", "OR"), ("@OR", "OR"), (">", "GT"),
    ("@GT", "GT"), ("@LT", "LT"), ("%", "MOD"), (">=", "GEQ"), ("@GEQ", "GEQ"), ("@LEQ", "LEQ"),
    ("+", "PLUS"), ("=", "ASSIGN"), ("-", "MINUS"), ("**", "EXPO"), ("*", "MULT"), ("/", "DIV"),
    ("!", "NOT"),
]


def _longest(rx, s, i):
    """Length of the longest prefix of s[i:] fully matched by rx (DFA semantics)."""
    best = -1
    for j in range(len(s), i, -1):
        if rx.fullmatch(s, i, j):
            return j - i
    return best


class _Lexer:
    def __init__(self, s):
        self.s = s
        self.i = 0
        self.lexeme = ""
        self.lexeme_start = 0

    def token(self):
        s = self.s
        while True:
            if self.i >= len(s):
                self.lexeme = ""
                self.lexeme_start = self.i
                return ("EOF",)
            best_len, best_kind = 0, None
            for rx, kind in _RULES:
                n = _longest(rx, s, self.i)
                if n > best_len:
                    best_len, best_kind = n, kind
            for lit, kind in _FIXED:
                if s.startswith(lit, self.i) and len(lit) > best_len:
                    best_len, best_kind = len(lit), kind
            if best_kind is None:
                self.lexeme_start = self.i
                raise LexError(self.i)
            lex = s[self.i:self.i + best_len]
            self.lexeme = lex
            self.lexeme_start = self.i
            self.i += best_len
            if best_kind == "SKIP":
                continue
            if best_kind == "INT":
                try:
                    return ("INT", int_of_string(lex))
                except Failure:
                    raise LexError(self.lexeme_start)
            if best_kind == "FLOAT":
                return ("FLOAT", float_of_string(lex))
            if best_kind == "IDENT":
                return ("IDENT", lex)
            if best_kind == "QIDENT":
                return ("IDENT", lex[1:-1])
            return (best_kind,)


_BINOPS = {
    "AND": (1, "&&"), "OR": (1, "||"),
    "EQ": (2, "=="), "GT": (2, ">"), "LT": (2, "<"), "GEQ": (2, ">="), "LEQ": (2, "<="),
    "PLUS": (3, "+"), "MINUS": (3, "-"),
    "MULT": (4, "*"), "DIV": (4, "/"), "MOD": (4, "%"), "EXPO": (4, "**"),
}
_PREC_NOT, _PREC_UMINUS, _PREC_DEREF = 5, 6, 7


class _Parser:
    """Precedence climbing equivalent to the ocamlyacc LALR(1) grammar."""

    def __init__(self, s):
        self.lx = _Lexer(s)
        self.tok = None

    def next(self):
        self.tok = self.lx.token()

    def error(self):
        raise ParseError(self.lx.lexeme)

    def parse(self):
        self.next()
        e = self.expression(0)
        if self.tok[0] != "EOF":  # ocamlyacc entry point only accepts on EOF
            self.error()
        return e

    def expression(self, min_prec):
        left = self.unary()
        while True:
            k = self.tok[0]
            if k == "DEREF":
                if _PREC_DEREF <= min_prec:
                    return left
                self.next()
                if self.tok[0] != "IDENT":
                    self.error()
                left = ("deref", left, self.tok[1])
                self.next()
                continue
            if k in _BINOPS:
                prec, op = _BINOPS[k]
                if prec <= min_prec:
                    return left
                self.next()
                right = self.expression(prec)
                left = ("op", op, [left, right])
                continue
            return left

    def unary(self):
        t = self.tok
        k = t[0]
        if k == "MINUS":
            self.next()
            return ("op", "-", [self.expression(_PREC_UMINUS)])
        if k == "NOT":
            self.next()
            return ("op", "!", [self.expression(_PREC_NOT)])
        if k == "INT":
            self.next()
            return ("int", t[1])
        if k == "FLOAT":
            self.next()
            return ("float", t[1])
        if k == "LP":
            self.next()
            e = self.expression(0)
            if self.tok[0] != "RP":
                self.error()
            self.next()
            return e
        if k == "IDENT":
            name = t[1]
            self.next()
            k2 = self.tok[0]
            if k2 == "DOT":
                self.next()
                if self.tok[0] != "IDENT":
                    self.error()
                f = self.tok[1]
                self.next()
                return ("field", name, f)
            if k2 == "LP":
                self.next()
                args = []
                if self.tok[0] != "RP":
                    args.append(self.expression(0))
                    while self.tok[0] == "COMMA":
                        self.next()
                        args.append(self.expression(0))
                if self.tok[0] != "RP":
                    self.error()
                self.next()
                return ("call", name, args)
            if k2 == "LB":
                self.next()
                e = self.expression(0)
                if self.tok[0] != "RB":
                    self.error()
                self.next()
                return ("index", name, e)
            return ("ident", name)
        self.error()


def parse_raw(s):
    """Parse; raises LexError / ParseError / Failure."""
    return _Parser(s).parse()


def parse_expression(s, prefix=""):
    """Fp_proc.parse_expression / Expr_lexer.parse: errors exit(1)."""
    p = _Parser(s)
    try:
        return p.parse()
    except LexError as e:
        ch = s[e.pos] if e.pos < len(s) else "\x00"
        sys.stderr.write("Lexing error in '%s': unexpected char: '%s' \n" % (s, ch))
        sys.stderr.flush()
        raise SystemExit(1)
    except Failure:
        ch = s[p.lx.lexeme_start] if p.lx.lexeme_start < len(s) else "\x00"
        sys.stderr.write("Lexing error in '%s': unexpected char: '%s' \n" % (s, ch))
        sys.stderr.flush()
        raise SystemExit(1)
    except ParseError as e:
        sys.stderr.write("%sParsing error in '%s', token '%s' ?\n" % (prefix, s, e.lexeme))
        sys.stderr.flush()
        raise SystemExit(1)


def c_var_of_ident(x):
    return "_var_" + x


def sprint(expr, call_assoc=None):
    n, l = (None, []) if call_assoc is None else call_assoc

    def ev(e):
        k = e[0]
        if k == "ident":
            i = e[1]
            return c_var_of_ident(i[1:]) if i[0] == "$" else i
        if k == "int":
            return "%d" % e[1]
        if k == "float":
            return "%f" % e[1]
        if k == "op":
            op, args = e[1], e[2]
            if len(args) == 2:
                return "(%s%s%s)" % (ev(args[0]), op, ev(args[1]))
            if len(args) == 1:
                return "%s(%s)" % (op, ev(args[0]))
            raise Failure("Operator should be binary or unary")
        if k == "call":
            i, es = e[1], e[2]
            if n is not None and i == n and len(es) == 1 and es[0][0] == "ident":
                s = es[0][1]
                for a, idx in l:
                    if a == s:
                        return "%d" % idx
                raise Failure("Expr_syntax call_assoc not found: '%s'" % s)
            return "%s(%s)" % (i, ",".join(ev(x) for x in es))
        if k == "index":
            return "%s[%s]" % (e[1], ev(e[2]))
        if k == "field":
            return "%s.%s" % (e[1], e[2])
        if k == "deref":
            return "(%s)->%s" % (ev(e[1]), e[2])
        raise AssertionError(e)

    return ev(expr)


FUNCTIONS = ["Qdr", "And", "Or", "RcRoll", "RcEvent1", "RcEvent2", "RadOfDeg"]
VARIABLES = ["launch", "estimator_z", "autopilot_flight_time", "estimator_hspeed_mod", "estimator_theta",
             "circle_count", "vsupply", "stage_time", "stage_time_ds", "block_time", "SECURITY_ALT",
             "ground_alt", "GROUND_ALT", "TRUE", "FALSE", "QFU", "gps_mode", "gps_utm_east",
             "gps_utm_north", "gps_utm_zone", "nav_utm_east0", "nav_utm_north0", "nav_utm_zone0",
             "cruise_throttle", "gps_lost"]


def _unexpected(kind, x):
    sys.stderr.write("Warning: unexpected %s in expression: '%s' \n" % (kind, x))


def check_expression(e):
    k = e[0]
    if k == "ident":
        if e[1][0] != "$" and e[1] not in VARIABLES:
            _unexpected("ident", e[1])
    elif k in ("int", "float", "op"):
        pass
    elif k == "call":
        if e[1] not in FUNCTIONS:
            _unexpected("function", e[1])
        for x in e[2]:
            check_expression(x)
    elif k == "index":
        if e[1] not in VARIABLES:
            _unexpected("ident", e[1])
        check_expression(e[2])
    elif k == "field":
        if e[1] not in VARIABLES:
            _unexpected("ident", e[1])
    elif k == "deref":
        check_expression(e[1])
