"""Port of sw/lib/ocaml/extXml.ml (helpers on top of xml_light)."""

from . import xml_light as X
from .ocaml import Failure, NotFound, int_of_string, float_of_string


class ExtXmlError(Exception):
    """ExtXml.Error"""


def child(xml, c, select=None):
    """First child element with tag `c` (and `select`), or the child at
    numeric index `c`. Raises NotFound."""
    kids = X.children(xml)
    try:
        idx = int_of_string(c)
    except Failure:
        idx = None
    if idx is not None:
        if idx < 0 or idx >= len(kids):
            raise IndexError("index out of bounds")
        return kids[idx]
    for e in kids:
        if isinstance(e, X.Element) and e.tag == c:
            if select is None or select(e):
                return e
    raise NotFound(c)


def get(xml, path):
    p = [x for x in path.split(".") if x != ""]
    if not p:
        raise Failure("ExtXml.get: empty path")
    cur = xml
    for x in p[:-1]:
        cur = child(cur, x)
    try:
        if X.tag(cur) != p[-1]:
            raise NotFound(path)
    except X.NotElement:
        raise NotFound(path)
    return cur


def get_attrib(xml, path, attr):
    return X.attrib(get(xml, path), attr)


def sprint_fields(attrs):
    s = ">"
    for a, b in reversed(attrs):
        s = f'{a}="{b}" ' + s
    return "<" + s


def attrib(xml, attr):
    try:
        return X.attrib(xml, attr)
    except X.NoAttribute:
        raise ExtXmlError(f"Error: Attribute '{attr}' expected in {sprint_fields(X.attribs(xml))}")


def attrib_opt(xml, attr):
    try:
        return X.attrib(xml, attr)
    except X.NoAttribute:
        return None


def attrib_opt_int(xml, attr):
    v = attrib_opt(xml, attr)
    return None if v is None else int_of_string(v)


def attrib_opt_float(xml, attr):
    v = attrib_opt(xml, attr)
    return None if v is None else float_of_string(v)


def tag_is(x, v):
    return X.tag(x).lower() == v.lower()


def attrib_or_default(x, a, default):
    try:
        return X.attrib(x, a)
    except (X.NoAttribute, X.NotElement):
        return default


def float_attrib(xml, a):
    v = attrib(xml, a)
    try:
        return float_of_string(v)
    except Exception:
        raise Failure(f"Error: float expected in '{v}'")


def int_attrib(xml, a):
    v = attrib(xml, a)
    try:
        return int_of_string(v)
    except Exception:
        raise Failure(f"Error: integer expected in '{v}'")


def parse_children(tagname, f, kids):
    """NOTE: like the OCaml version, returns results in REVERSE order."""
    out = []
    for x in kids:
        if X.tag(x) == tagname:
            out.insert(0, f(x))
    return out


def iter_tag(tagname, f, xml):
    for x in X.children(xml):
        if tag_is(x, tagname):
            f(x)


def filter_tag(tagname, xml):
    acc = []
    for x in X.children(xml):
        if tag_is(x, tagname):
            acc.insert(0, x)
    return acc


def partition_tag(tagname, xmls):
    yes, no = [], []
    for x in xmls:
        if tag_is(x, tagname):
            yes.insert(0, x)
        else:
            no.insert(0, x)
    return yes, no


def remove_child(t, xml, select=lambda _x: True):
    if not isinstance(xml, X.Element):
        return xml
    return X.Element(xml.tag, xml.attribs,
                     [c for c in xml.children if not (tag_is(c, t) and select(c))])


def subst_attrib(attr, value, xml):
    if not isinstance(xml, X.Element):
        return xml
    ua = attr.upper()
    out = [c for c in xml.attribs if c[0].upper() != ua]
    out.append((attr, value))
    return X.Element(xml.tag, out, xml.children)


def subst_child(t, x, xml, select=lambda _x: True):
    if not isinstance(xml, X.Element):
        return xml
    found = False
    kids = []
    for c in xml.children:
        if tag_is(c, t) and select(c):
            found = True
            kids.append(x)
        else:
            kids.append(c)
    if not found:
        raise NotFound(t)
    return X.Element(xml.tag, xml.attribs, kids)


def subst_or_add_child(t, x, xml):
    try:
        return subst_child(t, x, xml)
    except NotFound:
        if isinstance(xml, X.Element):
            return X.Element(xml.tag, xml.attribs, [x] + xml.children)
        return xml


# ---- printing


def _attr_fmt(n, v, indent, tab):
    s = (tab if indent else "") + " " + n + '="' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s + ("\n" if indent else "")


def _my_to_string_fmt(tab_attribs, x):
    out = []

    def loop(e, tab, newl=False):
        if isinstance(e, X.Element):
            if not e.children:
                out.append(tab + "<" + e.tag)
                if tab_attribs:
                    out.append("\n")
                for n, v in e.attribs:
                    out.append(_attr_fmt(n, v, tab_attribs, tab))
                if tab_attribs:
                    out.append(tab)
                out.append("/>")
            elif len(e.children) == 1 and isinstance(e.children[0], str):
                out.append(tab + "<" + e.tag)
                for n, v in e.attribs:
                    out.append(_attr_fmt(n, v, tab_attribs, tab))
                out.append(">" + e.children[0] + "</" + e.tag + ">")
            else:
                out.append(tab + "<" + e.tag)
                for n, v in e.attribs:
                    out.append(_attr_fmt(n, v, tab_attribs, tab))
                out.append(">\n")
                for c in e.children:
                    loop(c, tab + "  ", True)
                out.append(tab + "</" + e.tag + ">")
        else:
            out.append(e)
        if newl:
            out.append("\n")

    loop(x, "")
    return "".join(out)


def _lower(x):
    if not isinstance(x, X.Element):
        return x
    return X.Element(x.tag.lower(), [(a.lower(), v) for a, v in x.attribs],
                     [_lower(c) for c in x.children])


def to_string_fmt(xml, tab_attribs=False):
    """ExtXml.to_string_fmt: lowercased names, text NOT escaped."""
    return _my_to_string_fmt(tab_attribs, _lower(xml))


# ---- file parsing


def parse_file(fname, noprovedtd=False):
    # NOTE: in xml-light 2.5, Xml.Error and Xml.File_not_found (the ones the
    # OCaml code catches here) are never raised: the parser raises
    # Xml_light_errors.Xml_error / File_not_found instead. Only DTD errors
    # are actually converted to Failure; the others propagate (exit code 2).
    try:
        if noprovedtd:
            p = X.XmlParser()
            p.prove = False
            return p.parse_file(fname)
        return X.parse_file(fname)
    except X.DtdProveError as e:
        raise Failure(f"DTD prove error in {fname}: {e}")
    except X.DtdCheckError as e:
        raise Failure(f"DTD check error in {fname}: {e}")
    except X.DtdParseError as e:
        raise Failure(f"DTD parse error in {fname}: {e}")


def display_entities(s):
    return s.replace("&quot;", '"').replace("&amp;", "&")
