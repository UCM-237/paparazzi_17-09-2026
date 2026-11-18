"""
Faithful Python port of the xml-light 2.5 OCaml library (Nicolas Cannasse),
as used by the Paparazzi generators.

Paparazzi output (generated headers, the expanded aircraft configuration and
its MD5 signature) depends on xml-light's exact behaviour, so this module
reproduces it rather than using a standard XML parser:

- attribute values are NOT entity-decoded, only text is; inside attribute
  values a backslash escapes a quote or a backslash;
- text is lexed line by line and consecutive text chunks are joined with
  '\\n'; whitespace-only chunks are dropped, leading indentation is kept;
- when a document has a DOCTYPE, it is validated ("proved") against the DTD,
  which uppercases attribute names, adds DTD default values and reorders the
  attributes in descending alphabetical order of their uppercased names;
- all strings are handled as Latin-1 so arbitrary bytes round-trip unchanged.

Element nodes are `Element` objects, text nodes are plain `str`.
"""

import os

ENCODING = "latin-1"

# --------------------------------------------------------------------------
# Tree


class Element:
    __slots__ = ("tag", "attribs", "children")

    def __init__(self, tag, attribs=None, children=None):
        self.tag = tag
        self.attribs = list(attribs) if attribs else []  # list of (name, value)
        self.children = list(children) if children else []  # Element | str

    def __repr__(self):
        return f"<Element {self.tag} {self.attribs} ({len(self.children)} children)>"

    def __eq__(self, other):
        return (isinstance(other, Element) and self.tag == other.tag
                and self.attribs == other.attribs and self.children == other.children)

    __hash__ = None


class XmlError(Exception):
    """Lexing/parsing error (Xml.Error)."""


class DtdParseError(Exception):
    pass


class DtdCheckError(Exception):
    pass


class DtdProveError(Exception):
    pass


class FileNotFound(Exception):
    def __init__(self, filename):
        super().__init__(filename)
        self.filename = filename


class NoAttribute(Exception):
    pass


class NotElement(Exception):
    pass


def is_element(x):
    return isinstance(x, Element)


def tag(x):
    if not isinstance(x, Element):
        raise NotElement(x)
    return x.tag


def attribs(x):
    if not isinstance(x, Element):
        raise NotElement(x)
    return x.attribs


def children(x):
    if not isinstance(x, Element):
        raise NotElement(x)
    return x.children


def attrib(x, att):
    """Case-insensitive attribute lookup (Xml.attrib)."""
    if not isinstance(x, Element):
        raise NotElement(x)
    la = att.lower()
    for n, v in x.attribs:
        if n.lower() == la:
            return v
    raise NoAttribute(la)


def pcdata(x):
    if isinstance(x, Element):
        raise NotElement(x)
    return x


# --------------------------------------------------------------------------
# Lexer

_ENTITIES = {"gt;": ">", "lt;": "<", "amp;": "&", "apos;": "'", "quot;": '"',
             "#62;": ">", "#60;": "<", "#38;": "&", "#39;": "'", "#34;": '"'}

_IDENTCHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_0123456789:-")
_ENTITYCHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789")
_HEXDIGITS = frozenset("ABCDEFabcdef0123456789")
_DIGITS = frozenset("0123456789")
_NOT_PCCHAR = frozenset("\r\n<>&")

# tokens
T_TAG, T_PCDATA, T_ENDTAG, T_DOCTYPE, T_EOF = range(5)
DTD_FILE, DTD_DATA = 0, 1


class _Lexer:
    def __init__(self, text):
        self.s = text
        self.i = 0
        self.n = len(text)
        self.line = 1

    # --- error helpers
    def error(self, msg):
        raise XmlError(f"{msg} line {self.line}")

    def dtd_error(self, msg):
        raise DtdParseError(f"{msg} line {self.line}")

    def startswith(self, p):
        return self.s.startswith(p, self.i)

    def peek(self):
        return self.s[self.i] if self.i < self.n else ""

    # --- rule ignore_spaces
    def ignore_spaces(self):
        s, n = self.s, self.n
        while self.i < n:
            c = s[self.i]
            if c == "\n":
                self.line += 1
            elif c not in " \t\r":
                break
            self.i += 1

    def ident_name(self, err="Ident expected"):
        j = self.i
        s, n = self.s, self.n
        while j < n and s[j] in _IDENTCHARS:
            j += 1
        if j == self.i:
            self.error(err)
        r = s[self.i:j]
        self.i = j
        return r

    # --- rule token
    def token(self):
        s, n = self.s, self.n
        while True:
            if self.i >= n:
                return (T_EOF,)
            c = s[self.i]
            if c == "\n":
                self.line += 1
                self.i += 1
                continue
            if c in " \t\r":
                # (space|break)+ versus space* pcchar+ : longest match, first rule on tie
                j = self.i
                while j < n and s[j] in " \t\r":
                    j += 1
                k = self.i
                while k < n and s[k] not in _NOT_PCCHAR:
                    k += 1
                if k > j:
                    return self._pcdata_start(k)
                self.i = j
                continue
            if c == "<":
                if self.startswith("<!DOCTYPE"):
                    self.i += 9
                    self.ignore_spaces()
                    root = self.ident_name()
                    self.ignore_spaces()
                    return (T_DOCTYPE, root, self.dtd_data())
                if self._match_cdata_start():
                    return (T_PCDATA, self.cdata())
                if self.startswith("<!--"):
                    self.i += 4
                    self.comment()
                    continue
                if self.startswith("<?"):
                    self.i += 2
                    self.header()
                    continue
                j = self.i + 1
                while j < n and s[j] in " \t":
                    j += 1
                if j < n and s[j] == "/":
                    j += 1
                    while j < n and s[j] in " \t":
                        j += 1
                    self.i = j
                    t = self.ident_name()
                    self.ignore_spaces()
                    if self.peek() != ">":
                        self.error("Close expected")
                    self.i += 1
                    return (T_ENDTAG, t)
                self.i = j
                t = self.ident_name()
                self.ignore_spaces()
                attrs, closed = self.attributes()
                return (T_TAG, t, attrs, closed)
            if c == "&":
                self.i += 1
                buf = [self.entity()]
                return (T_PCDATA, self._pcdata(buf))
            if c not in _NOT_PCCHAR:
                k = self.i
                while k < n and s[k] not in _NOT_PCCHAR:
                    k += 1
                return self._pcdata_start(k)
            self.error("Node expected")

    def _pcdata_start(self, k):
        buf = [self.s[self.i:k]]
        self.i = k
        return (T_PCDATA, self._pcdata(buf))

    def _pcdata(self, buf):
        s, n = self.s, self.n
        while self.i < n:
            c = s[self.i]
            if c == "&":
                self.i += 1
                buf.append(self.entity())
            elif c in _NOT_PCCHAR:
                break
            else:
                k = self.i
                while k < n and s[k] not in _NOT_PCCHAR:
                    k += 1
                buf.append(s[self.i:k])
                self.i = k
        return "".join(buf)

    def _match_cdata_start(self):
        if not self.startswith("<!["):
            return False
        w = self.s[self.i + 3:self.i + 9]
        if len(w) == 6 and w[:5].lower() == "cdata" and w[5] == "[":
            self.i += 9
            return True
        return False

    def entity(self):
        s, n, i = self.s, self.n, self.i
        j = i
        if j < n and s[j] in _ENTITYCHARS:
            while j < n and s[j] in _ENTITYCHARS:
                j += 1
            if j < n and s[j] == ";":
                return self._entity_lookup(j + 1)
        # "#x" hexdigit+ ';'  |  '#' digit+ ';'
        if s.startswith("#x", i):
            j = i + 2
            k = j
            while k < n and s[k] in _HEXDIGITS:
                k += 1
            if k > j and k < n and s[k] == ";":
                return self._entity_lookup(k + 1)
        if i < n and s[i] == "#":
            j = i + 1
            k = j
            while k < n and s[k] in _DIGITS:
                k += 1
            if k > j and k < n and s[k] == ";":
                return self._entity_lookup(k + 1)
        raise XmlError(f"Unterminated entity line {self.line}")

    def _entity_lookup(self, end):
        ident = self.s[self.i:end]
        self.i = end
        r = _ENTITIES.get(ident.lower())
        return r if r is not None else "&" + ident

    def comment(self):
        j = self.s.find("-->", self.i)
        if j < 0:
            raise XmlError(f"Unterminated comment line {self.line}")
        self.line += self.s.count("\n", self.i, j)
        self.i = j + 3

    def header(self):
        j = self.s.find("?>", self.i)
        if j < 0:
            self.i = self.n
            self.error("Close expected")
        self.line += self.s.count("\n", self.i, j)
        self.i = j + 2

    def cdata(self):
        j = self.s.find("]]>", self.i)
        if j < 0:
            self.i = self.n
            self.error("Close expected")
        r = self.s[self.i:j]
        self.line += r.count("\n")
        self.i = j + 3
        return r

    def attributes(self):
        attrs = []
        while True:
            if self.startswith(">"):
                self.i += 1
                return attrs, False
            if self.startswith("/>"):
                self.i += 2
                return attrs, True
            key = self.ident_name("Attribute name expected")
            data = self.attribute_data()
            self.ignore_spaces()
            attrs.append((key, data))

    def attribute_data(self):
        s, n = self.s, self.n
        j = self.i
        while j < n and s[j] in " \t":
            j += 1
        if j < n and s[j] == "=":
            j += 1
            while j < n and s[j] in " \t":
                j += 1
            if j < n and s[j] in "\"'":
                q = s[j]
                self.i = j + 1
                return self.quoted(q)
        self.error("Attribute value expected")

    def quoted(self, q):
        s, n = self.s, self.n
        buf = []
        while True:
            if self.i >= n:
                raise XmlError(f"Unterminated string line {self.line}")
            c = s[self.i]
            if c == q:
                self.i += 1
                return "".join(buf)
            if c == "\\" and self.i + 1 < n and s[self.i + 1] in (q, "\\"):
                buf.append(s[self.i + 1])
                self.i += 2
                continue
            buf.append(c)
            self.i += 1

    # ---------------- DTD part
    def dtd_data(self):
        if self.startswith("PUBLIC"):
            self.i += 6
            self.ignore_spaces()
            self.dtd_file()
            f = self.dtd_file()
            self.dtd_end_decl()
            return (DTD_FILE, f)
        if self.startswith("SYSTEM"):
            self.i += 6
            self.ignore_spaces()
            f = self.dtd_file()
            self.dtd_end_decl()
            return (DTD_FILE, f)
        if self.startswith("["):
            self.i += 1
            self.ignore_spaces()
            data = self.dtd_intern()
            self.dtd_end_decl()
            return (DTD_DATA, data)
        self.dtd_error("Invalid DOCTYPE declaration")

    def dtd_file(self):
        c = self.peek()
        if c in ("\"", "'") and c:
            self.i += 1
            try:
                v = self.quoted(c)
            except XmlError:
                raise
            self.ignore_spaces()
            return v
        self.dtd_error("Invalid DOCTYPE declaration")

    def dtd_end_decl(self):
        if self.peek() == ">":
            self.i += 1
            self.ignore_spaces()
            return
        self.dtd_error("Invalid DOCTYPE declaration")

    def dtd_intern(self):
        items = []
        while True:
            if self.peek() == "]":
                self.i += 1
                self.ignore_spaces()
                return items
            items.extend(self.dtd_item())

    def dtd(self):
        items = []
        while True:
            if self.i >= self.n:
                return items
            c = self.s[self.i]
            if c == "\n":
                self.line += 1
                self.i += 1
                continue
            if c in " \t\r":
                self.i += 1
                continue
            items.extend(self.dtd_item())

    def dtd_item(self):
        if self.startswith("<!--"):
            self.i += 4
            self.comment()
            return []
        if self.startswith("<!"):
            self.i += 2
            self.ignore_spaces()
            if self.startswith("ELEMENT"):
                self.i += 7
                self.ignore_spaces()
                is_elt = True
            elif self.startswith("ATTLIST"):
                self.i += 7
                self.ignore_spaces()
                is_elt = False
            else:
                self.dtd_error("Invalid DTD tag")
            try:
                name = self.ident_name()
            except XmlError:
                self.dtd_error("Invalid DOCTYPE declaration")
            self.ignore_spaces()
            if is_elt:
                return [("element", name, self.dtd_element_type())]
            return [("attribute", name, an, at, ad) for (an, at, ad) in self.dtd_attributes()]
        self.dtd_error("DTD item expected")

    def dtd_attributes(self):
        res = []
        while True:
            if self.peek() == ">":
                self.i += 1
                self.ignore_spaces()
                return res
            try:
                an = self.ident_name()
            except XmlError:
                self.dtd_error("Invalid DTD attribute declaration")
            self.ignore_spaces()
            at = self.dtd_attr_type()
            ad = self.dtd_attr_default()
            res.append((an, at, ad))

    def dtd_element_type(self):
        if self.startswith("ANY"):
            self.i += 3
            self.ignore_spaces()
            self._dtd_end_element()
            return ("any",)
        if self.startswith("EMPTY"):
            self.i += 5
            self.ignore_spaces()
            self._dtd_end_element()
            return ("empty",)
        if self.peek() == "(":
            self.i += 1
            item = _DtdElementParser(self).parse()
            self.ignore_spaces()
            return ("child", item)
        self.dtd_error("Invalid DTD element declaration")

    def _dtd_end_element(self):
        if self.peek() == ">":
            self.i += 1
            self.ignore_spaces()
            return
        self.dtd_error("Invalid DTD element declaration")

    def dtd_element_token(self):
        s, n = self.s, self.n
        while self.i < n:
            c = s[self.i]
            if c == "\n":
                self.line += 1
                self.i += 1
            elif c in " \t\r":
                self.i += 1
            else:
                break
        if self.i >= n:
            self.dtd_error("Invalid DTD element declaration")
        c = s[self.i]
        simple = {"(": "OPEN", ")": "CLOSE", ",": "NEXT", ">": "END", "|": "OR",
                  "*": "STAR", "+": "PLUS", "?": "QUESTION"}
        if c in simple:
            self.i += 1
            return (simple[c],)
        if self.startswith("#PCDATA"):
            self.i += 7
            return ("PCDATA",)
        if c in _IDENTCHARS:
            j = self.i
            while j < n and s[j] in _IDENTCHARS:
                j += 1
            v = s[self.i:j]
            self.i = j
            return ("IDENT", v)
        self.dtd_error("Invalid DTD element declaration")

    def dtd_attr_type(self):
        # ocamllex longest match: IDREF before ID
        for kw, v in (("CDATA", ("cdata",)), ("NMTOKEN", ("nmtoken",)),
                      ("IDREF", ("idref",)), ("ID", ("id",))):
            if self.startswith(kw):
                self.i += len(kw)
                self.ignore_spaces()
                return v
        if self.peek() == "(":
            self.i += 1
            self.ignore_spaces()
            return ("enum", self.dtd_attr_enum())
        self.dtd_error("Invalid DTD attribute declaration")

    def dtd_attr_enum(self):
        vals = []
        while True:
            j = self.i
            while j < self.n and self.s[j] in _IDENTCHARS:
                j += 1
            if j == self.i:
                self.dtd_error("Invalid DTD attribute declaration")
            vals.append(self.s[self.i:j])
            self.i = j
            self.ignore_spaces()
            c = self.peek()
            if c == ")":
                self.i += 1
                self.ignore_spaces()
                return vals
            if c == "|":
                self.i += 1
                self.ignore_spaces()
                continue
            self.dtd_error("Invalid DTD attribute declaration")

    def _dtd_attr_string(self):
        c = self.peek()
        if c in ("\"", "'") and c:
            self.i += 1
            try:
                v = self.quoted(c)
            except XmlError:
                self.dtd_error("Invalid DTD attribute declaration")
            self.ignore_spaces()
            return v
        self.dtd_error("Invalid DTD attribute declaration")

    def dtd_attr_default(self):
        c = self.peek()
        if c in ("\"", "'") and c:
            return ("default", self._dtd_attr_string())
        for kw in ("#REQUIRED", "#IMPLIED"):
            if self.startswith(kw):
                self.i += len(kw)
                self.ignore_spaces()
                return ("required",) if kw == "#REQUIRED" else ("implied",)
        if self.startswith("#FIXED"):
            self.i += 6
            self.ignore_spaces()
            return ("fixed", self._dtd_attr_string())
        if self.startswith("#DEFAULT"):
            self.i += 8
            self.ignore_spaces()
            return ("default", self._dtd_attr_string())
        self.dtd_error("Invalid DTD attribute declaration")


class _DtdElementParser:
    """Recursive-descent version of xml-light's ocamlyacc dtd_element grammar.

    Content model nodes: ("tag", name) ("pcdata",) ("optional", x)
    ("zero_or_more", x) ("one_or_more", x) ("choice", [..]) ("children", [..])
    """

    def __init__(self, lexer):
        self.lx = lexer
        self.tok = None

    def next(self):
        self.tok = self.lx.dtd_element_token()

    def fail(self):
        self.lx.dtd_error("Invalid DTD element declaration")

    def parse(self):
        self.next()
        r = self.full_seq()
        if self.tok[0] != "END":
            self.fail()
        return r

    def full_seq(self):
        seq = self.seq()
        if self.tok[0] != "CLOSE":
            self.fail()
        self.next()
        return self.ops(seq)

    def ops(self, x):
        while self.tok[0] in ("STAR", "QUESTION", "PLUS"):
            k = self.tok[0]
            x = (("zero_or_more", x) if k == "STAR" else
                 ("optional", x) if k == "QUESTION" else ("one_or_more", x))
            self.next()
        return x

    def seq(self):
        first = self.item()
        if self.tok[0] == "NEXT":
            items = [first]
            while self.tok[0] == "NEXT":
                self.next()
                items.append(self.item())
            if self.tok[0] == "OR":
                self.fail()
            return ("children", items)
        if self.tok[0] == "OR":
            items = [first]
            while self.tok[0] == "OR":
                self.next()
                items.append(self.item())
            if self.tok[0] == "NEXT":
                self.fail()
            return ("choice", items)
        return first

    def item(self):
        t = self.tok
        if t[0] == "OPEN":
            self.next()
            return self.full_seq()
        if t[0] == "IDENT":
            self.next()
            return self.ops(("tag", t[1]))
        if t[0] == "PCDATA":
            self.next()
            return self.ops(("pcdata",))
        self.fail()


# --------------------------------------------------------------------------
# DTD check / prove


class CheckedDtd:
    def __init__(self, elements, attribs):
        self.elements = elements  # utag -> element type
        self.attribs = attribs    # utag -> {uattr: (type, default)}


def dtd_check(items):
    attribs = {}
    hdone = {}
    htodo = {}

    def ftodo(t, frm):
        if t in hdone:
            return
        if t not in htodo or htodo[t] is None:
            htodo[t] = frm

    for it in items:
        if it[0] == "attribute":
            _, t, an, at, ad = it
            ut = t.upper()
            ftodo(ut, None)
            ua = an.upper()
            if at == ("id",) and ad[0] not in ("implied", "required"):
                raise DtdCheckError(
                    f"Attribute '{ua}' of type ID of element '{ut}' not defined with implicit "
                    "value #REQUIRED or #IMPLIED")
            h = attribs.setdefault(ut, {})
            if ua in h:
                raise DtdCheckError(f"Attribute '{ua}' of element '{ut}' defined twice")
            h[ua] = (at, ad)
        else:
            _, t, et = it
            ut = t.upper()
            if ut in hdone:
                raise DtdCheckError(f"Element '{ut}' defined twice")
            htodo.pop(ut, None)
            hdone[ut] = et
            if et[0] == "child":
                def check_child(c):
                    k = c[0]
                    if k == "tag":
                        ftodo(c[1].upper(), ut)
                    elif k == "pcdata":
                        pass
                    elif k in ("optional", "zero_or_more", "one_or_more"):
                        check_child(c[1])
                    else:
                        if not c[1]:
                            raise DtdCheckError(f"Element '{t}' has empty constructor")
                        for x in c[1]:
                            check_child(x)
                check_child(et[1])
    for t in sorted(htodo):
        frm = htodo[t]
        if frm is None:
            raise DtdCheckError(f"Element '{t}' needed but is not declared")
        raise DtdCheckError(f"Element '{t}' referenced by '{frm}' is not declared")
    return CheckedDtd(hdone, attribs)


def dtd_parse_file(fname):
    try:
        with open(fname, "rb") as f:
            text = f.read().decode(ENCODING)
    except OSError:
        raise FileNotFound(fname)
    lx = _Lexer(text)
    try:
        return lx.dtd()
    except XmlError as e:  # nested lexer errors are DTD errors here
        raise DtdParseError(str(e))


_NOT_MATCHED, _NEXT, _MATCHED = "not_matched", "next", "matched"


class _ProveState:
    def __init__(self, dtd, root):
        self.elements = dtd.elements
        self.attribs = dtd.attribs
        self.current = ("child", ("tag", root))
        self.stack = []
        if root.upper() not in self.elements:
            raise DtdCheckError(f"Element '{root}' needed but is not declared")
        self.hid = set()
        self.hidref = set()

    def prove_child(self, tagname):
        cur = self.current
        if cur[0] == "empty":
            raise DtdProveError("Empty element expected")
        if cur[0] == "any":
            return

        def update(e):
            k = e[0]
            if k == "tag":
                if tagname is None:
                    return (_NOT_MATCHED,)
                return (_MATCHED,) if tagname == e[1].upper() else (_NOT_MATCHED,)
            if k == "pcdata":
                return (_MATCHED,) if tagname is None else (_NOT_MATCHED,)
            if k == "optional":
                r = update(e[1])
                return (_NEXT,) if r[0] in (_NOT_MATCHED, _NEXT) else (_MATCHED,)
            if k == "zero_or_more":
                r = update(e[1])
                if r[0] in (_NOT_MATCHED, _NEXT):
                    return (_NEXT,)
                return ("result", ("zero_or_more", e[1]))
            if k == "one_or_more":
                r = update(e[1])
                if r[0] in (_NOT_MATCHED, _NEXT):
                    return (_NOT_MATCHED,)
                return ("result", ("zero_or_more", e[1]))
            if k == "choice":
                for x in e[1]:
                    r = update(x)
                    if r[0] == _MATCHED:
                        return (_MATCHED,)
                    if r[0] == "result":
                        return r
                return (_NOT_MATCHED,)
            if k == "children":
                lst = e[1]
                h, t = lst[0], lst[1:]
                r = update(h)
                if r[0] == _NEXT:
                    if not t:
                        return (_NOT_MATCHED,)
                    return update(("children", t))
                if r[0] == _NOT_MATCHED:
                    return (_NOT_MATCHED,)
                if r[0] == "result":
                    return ("result", ("children", [r[1]] + t))
                if not t:
                    return (_MATCHED,)
                return ("result", ("children", t))
            raise AssertionError(k)

        r = update(cur[1])
        if r[0] in (_NEXT, _NOT_MATCHED):
            if tagname is None:
                raise DtdProveError("Unexpected PCData")
            raise DtdProveError(f"Unexpected tag : '{tagname}'")
        if r[0] == _MATCHED:
            self.current = ("empty",)
        else:
            self.current = ("child", r[1])


def _is_nmtoken(v):
    return all(c in _IDENTCHARS or c == "." for c in v)


def _prove_attribs(st, utag, tagname, uattr):
    ahash = st.attribs.get(utag, {})
    for an, _ in uattr:
        if an not in ahash:
            raise DtdProveError(f"Unexpected attribute : '{an}'")
    lookup = {}
    for an, av in uattr:  # List.assoc: first occurrence wins
        lookup.setdefault(an, av)
    accu = []
    for an in sorted(ahash):  # StringMap.fold: ascending keys, result prepended
        atype, adef = ahash[an]
        aval = lookup.get(an)
        if atype == ("nmtoken",) and aval is not None and not _is_nmtoken(aval):
            raise DtdProveError(f"Invalid attribute value for '{an}'")
        if atype[0] == "enum" and aval is not None and aval not in atype[1]:
            raise DtdProveError(f"Invalid attribute value for '{an}'")
        if atype == ("id",) and aval is not None:
            if aval in st.hid:
                raise DtdProveError(f"ID '{aval}' used several times")
            st.hid.add(aval)
        if atype == ("idref",) and aval is not None:
            st.hidref.add(aval)
        dk = adef[0]
        if dk == "required" and aval is None:
            raise DtdProveError(f"Required attribute not found : '{an}'")
        if dk == "fixed" and aval is not None and aval != adef[1]:
            raise DtdProveError(f"Invalid attribute value for '{an}'")
        if dk == "implied" and aval is None:
            continue
        v = aval if aval is not None else adef[1]
        accu.insert(0, (an, v))
    return accu


def _do_prove(st, x):
    if not isinstance(x, Element):
        st.prove_child(None)
        return x
    tagname = x.tag
    utag = tagname.upper()
    uattr = [(a.upper(), v) for a, v in x.attribs]
    st.prove_child(utag)
    st.stack.append(st.current)
    if utag not in st.elements:
        raise DtdProveError(f"Unexpected tag : '{tagname}'")
    elt = st.elements[utag]
    st.current = elt
    new_attrs = _prove_attribs(st, utag, tagname, uattr)
    childs = [_do_prove(st, c) for c in x.children]
    cur = st.current
    if cur[0] == "child":
        name = [""]

        def check(e):
            k = e[0]
            if k == "tag":
                name[0] = e[1]
                return False
            if k == "pcdata":
                if not childs:
                    childs.append("")
                    return True
                name[0] = "#PCDATA"
                return False
            if k in ("optional", "zero_or_more"):
                return True
            if k == "one_or_more":
                check(e[1])
                return False
            if k == "choice":
                return any(check(c) for c in e[1])
            if k == "children":
                return all(check(c) for c in e[1])
            raise AssertionError(k)

        if not check(cur[1]):
            raise DtdProveError(f"Child expected : '{name[0]}'")
    st.current = st.stack.pop()
    return Element(tagname, new_attrs, childs)


def dtd_prove(dtd, root, xml):
    st = _ProveState(dtd, root)
    x = _do_prove(st, xml)
    for i in sorted(st.hidref):
        if i not in st.hid:
            raise DtdProveError(f"ID '{i}' used but not declared")
    return x


# --------------------------------------------------------------------------
# Parser


class _NoMoreData(Exception):
    pass


class XmlParser:
    """XmlParser.t: prove / resolve / check_eof / concat_pcdata settings."""

    def __init__(self):
        self.prove = True
        self.check_eof = True
        self.concat_pcdata = True
        self.resolve = None  # function filename -> CheckedDtd

    def parse_string(self, text):
        return _Parse(self, _Lexer(text)).run()

    def parse_file(self, fname):
        try:
            with open(fname, "rb") as f:
                data = f.read()
        except OSError:
            raise FileNotFound(fname)
        return self.parse_string(data.decode(ENCODING))


class _Parse:
    def __init__(self, xparser, lexer):
        self.xp = xparser
        self.lx = lexer
        self.stack = []

    def pop(self):
        if self.stack:
            return self.stack.pop()
        return self.lx.token()

    def push(self, t):
        self.stack.append(t)

    def read_node(self):
        t = self.pop()
        if t[0] == T_PCDATA:
            return t[1]
        if t[0] == T_TAG:
            if t[3]:
                return Element(t[1], t[2], [])
            return Element(t[1], t[2], self.read_elems(t[1]))
        self.push(t)
        raise _NoMoreData

    def read_elems(self, tagname=None):
        elems = []
        while True:
            try:
                x = self.read_node()
            except _NoMoreData:
                break
            if self.xp.concat_pcdata and isinstance(x, str) and elems and isinstance(elems[-1], str):
                elems[-1] = f"{elems[-1]}\n{x}"
            else:
                elems.append(x)
        t = self.pop()
        if t[0] == T_ENDTAG and tagname is not None and t[1] == tagname:
            return elems
        if t[0] == T_EOF and tagname is None:
            return elems
        if tagname is None:
            raise XmlError(f"End of file expected line {self.lx.line}")
        raise XmlError(f"End of tag expected : '{tagname}' line {self.lx.line}")

    def read_xml(self):
        t = self.pop()
        if t[0] == T_DOCTYPE and self.xp.prove:
            root, (kind, data) = t[1], t[2]
            if kind == DTD_FILE:
                if self.xp.resolve is None:
                    raise FileNotFound(data)
                saved_line = self.lx.line
                dtd = self.xp.resolve(data)
                self.lx.line = saved_line
            else:
                dtd = dtd_check(data)
            x = self.read_node()
            return dtd_prove(dtd, root, x)
        if t[0] == T_DOCTYPE:
            return self.read_node()
        self.push(t)
        return self.read_node()

    def run(self):
        try:
            tk = self.pop()
            if tk != (T_PCDATA, "\xef\xbb\xbf"):
                self.push(tk)
            x = self.read_xml()
            if self.xp.check_eof and self.pop()[0] != T_EOF:
                raise XmlError(f"End of file expected line {self.lx.line}")
            return x
        except _NoMoreData:
            raise XmlError(f"Node expected line {self.lx.line}")


# Parsing is a pure function of the file contents: cache results (the
# generators parse the same module files and DTDs hundreds of times).
# Trees are never modified in place, so sharing them is safe.
_cache = {}


def _file_key(fname):
    try:
        st = os.stat(fname)
    except OSError:
        return None
    return (os.path.abspath(fname), st.st_mtime_ns, st.st_size)


def _cached(kind, fname, compute):
    key = _file_key(fname)
    if key is None:
        return compute()
    k = (kind,) + key
    r = _cache.get(k)
    if r is None:
        try:
            r = ("ok", compute())
        except Exception as e:
            r = ("err", e)
        _cache[k] = r
    if r[0] == "err":
        raise r[1]
    return r[1]


def _ocaml_dirname(name):
    """Filename.dirname (Unix)"""
    if name == "":
        return "."
    n = len(name) - 1
    while n >= 0 and name[n] == "/":
        n -= 1
    if n < 0:
        return name[0:1]
    while n >= 0 and name[n] != "/":
        n -= 1
    if n < 0:
        return "."
    while n >= 0 and name[n] == "/":
        n -= 1
    if n < 0:
        return name[0:1]
    return name[:n + 1]


def parse_file(fname):
    """Xml.parse_file: DTD files are resolved relative to the document."""
    def compute():
        p = XmlParser()
        path = _ocaml_dirname(fname)

        def resolve(f):
            name = f if path == "." else path + "/" + f
            return _cached("dtd", name, lambda: dtd_check(dtd_parse_file(name)))

        p.resolve = resolve
        return p.parse_file(fname)

    return _cached("xml", fname, compute)


def parse_string(text):
    return XmlParser().parse_string(text)


# --------------------------------------------------------------------------
# Printers (Xml.to_string / Xml.to_string_fmt)


def _pcdata_escaped(text):
    out = []
    n = len(text)
    for p, c in enumerate(text):
        if c == ">":
            out.append("&gt;")
        elif c == "<":
            out.append("&lt;")
        elif c == "&":
            out.append("&" if p < n - 1 and text[p + 1] == "#" else "&amp;")
        elif c == "'":
            out.append("&apos;")
        elif c == '"':
            out.append("&quot;")
        else:
            out.append(c)
    return "".join(out)


def _attr_escaped(v):
    return v.replace("\\", "\\\\").replace('"', '\\"')


def _attrs(alist):
    return "".join(f' {n}="{_attr_escaped(v)}"' for n, v in alist)


def to_string(x):
    out = []
    pc = [False]

    def loop(e):
        if isinstance(e, Element):
            if not e.children:
                out.append(f"<{e.tag}{_attrs(e.attribs)}/>")
            else:
                out.append(f"<{e.tag}{_attrs(e.attribs)}>")
                pc[0] = False
                for c in e.children:
                    loop(c)
                out.append(f"</{e.tag}>")
            pc[0] = False
        else:
            if pc[0]:
                out.append(" ")
            out.append(_pcdata_escaped(e))
            pc[0] = True

    loop(x)
    return "".join(out)


def to_string_fmt(x):
    out = []

    def loop(e, tab, newl=False):
        if isinstance(e, Element):
            if not e.children:
                out.append(f"{tab}<{e.tag}{_attrs(e.attribs)}/>")
            elif len(e.children) == 1 and isinstance(e.children[0], str):
                out.append(f"{tab}<{e.tag}{_attrs(e.attribs)}>{_pcdata_escaped(e.children[0])}</{e.tag}>")
            else:
                out.append(f"{tab}<{e.tag}{_attrs(e.attribs)}>\n")
                for c in e.children:
                    loop(c, tab + "  ", True)
                out.append(f"{tab}</{e.tag}>")
        else:
            out.append(_pcdata_escaped(e))
        if newl:
            out.append("\n")

    loop(x, "")
    return "".join(out)
