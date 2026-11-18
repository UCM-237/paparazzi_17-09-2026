"""Port of sw/lib/ocaml/fp_proc.ml: flight plan preprocessing
(procedure includes, paths, relative waypoints)."""

import re
import sys

from . import xml_light as X
from . import ext_xml, env, geo
from . import expr as E
from .ocaml import Failure, NotFound, string_of_float


def parse_expression(s):
    return E.parse_expression(s, prefix="Fp_proc: ")


def subst_expression(name_env, e):
    name, env_ = name_env

    def sub(e):
        k = e[0]
        if k == "ident":
            for a, v in env_:
                if a == e[1]:
                    return ("ident", v)
            return e
        if k in ("int", "float", "field"):
            return e
        if k == "call":
            if e[1] == "IndexOfBlock" and len(e[2]) == 1 and e[2][0][0] == "ident":
                return ("call", "IndexOfBlock", [("ident", "'" + name + "." + e[2][0][1] + "'")])
            return ("call", e[1], [sub(x) for x in e[2]])
        if k == "op":
            return ("op", e[1], [sub(x) for x in e[2]])
        if k == "index":
            return ("index", e[1], sub(e[2]))
        if k == "deref":
            return ("deref", sub(e[1]), e[2])
        raise AssertionError(e)

    return sub(e)


def transform_expression(name_env, e):
    return E.sprint(subst_expression(name_env, e))


def transform_values(not_modified, name_env, attribs):
    out = []
    for a, v in attribs:
        if a.lower() in not_modified:
            out.append((a, v))
        else:
            out.append((a, transform_expression(name_env, parse_expression(v))))
    return out


def prefix_or_deroute(prefix, reroutes, name, attribs):
    out = []
    for a, v in attribs:
        if a.lower() == name:
            for f, t in reroutes:
                if f == v:
                    v2 = t
                    break
            else:
                v2 = prefix(v)
            out.append((a, v2))
        else:
            out.append((a, v))
    return out


def transform_exception(prefix, reroutes, name_env, xml):
    if not isinstance(xml, X.Element):
        raise Failure("transform_exception")
    if xml.children:
        raise AssertionError("fp_proc.ml: transform_exception")
    attribs = prefix_or_deroute(prefix, reroutes, "deroute", xml.attribs)
    attribs = transform_values([], name_env, attribs)
    return X.Element(xml.tag, attribs, xml.children)


def transform_stage(prefix, reroutes, name_env, xml):
    def tr(xml):
        if not isinstance(xml, X.Element):
            raise Failure("Fp_proc: Xml.Element expected")
        tag, attribs, children = xml.tag, xml.attribs, xml.children
        t = tag.lower()

        def no_children():
            if children:
                raise AssertionError("fp_proc.ml: transform_stage")

        if t == "exception":
            return transform_exception(prefix, reroutes, name_env, xml)
        if t in ("while", "for"):
            return X.Element(tag, transform_values([], name_env, attribs), [tr(c) for c in children])
        if t == "heading":
            no_children()
            return X.Element(tag, transform_values(["vmode"], name_env, attribs), children)
        if t == "attitude":
            return X.Element(tag, transform_values(["vmode"], name_env, attribs), children)
        if t == "manual":
            return X.Element(tag, transform_values([], name_env, attribs), children)
        if t == "return":
            return X.Element(tag, transform_values(["reset_stage"], name_env, attribs), children)
        if t == "go":
            no_children()
            return X.Element(tag, transform_values(["wp", "from", "hmode", "vmode"], name_env, attribs), children)
        if t == "xyz":
            no_children()
            return X.Element(tag, transform_values([], name_env, attribs), children)
        if t == "circle":
            no_children()
            return X.Element(tag, transform_values(["wp", "hmode", "vmode"], name_env, attribs), children)
        if t == "eight":
            return X.Element(tag, transform_values(["center", "turn_around", "radius"], name_env, attribs), children)
        if t == "deroute":
            no_children()
            return X.Element(tag, prefix_or_deroute(prefix, reroutes, "block", attribs), children)
        if t == "stay":
            no_children()
            return X.Element(tag, transform_values(["wp", "vmode"], name_env, attribs), children)
        if t in ("call", "call_once", "set"):
            return X.Element(tag, transform_values(["var"], name_env, attribs), children)
        raise Failure("Fp_proc: Unexpected tag: '%s'" % tag)

    return tr(xml)


def transform_block(prefix, reroutes, name_env, xml):
    stages = [transform_stage(prefix, reroutes, name_env, c) for c in X.children(xml)]
    block = X.Element("block", X.attribs(xml), stages)
    return ext_xml.subst_attrib("name", prefix(ext_xml.attrib(xml, "name")), block)


def build_assocs(tag, key_attr, val_attr, xml):
    xmls = [x for x in X.children(xml) if ext_xml.tag_is(x, tag)]
    return [(ext_xml.attrib(x, key_attr), ext_xml.attrib(x, val_attr)) for x in xmls]


def get_children(tag, xml):
    try:
        return X.children(ext_xml.child(xml, tag))
    except NotFound:
        return []


def get_pc_data(tag, xml):
    try:
        return X.pcdata(ext_xml.child(ext_xml.child(xml, tag), "0"))
    except NotFound:
        return ""


def append_children(tag_children, xml):
    tag, new_children = tag_children
    children = get_children(tag, xml) + new_children
    return ext_xml.subst_or_add_child(tag, X.Element(tag, [], children), xml)


def append_pc_data(tag, new_data, xml):
    data = get_pc_data(tag, xml) + "\n" + new_data
    return ext_xml.subst_or_add_child(tag, X.Element(tag, [], [data]), xml)


def parse_include(dir_, flight_plan, include_xml):
    procedure = ext_xml.attrib(include_xml, "procedure")
    try:
        f = geo.find_file([dir_, env.flight_plans_path], procedure)
    except NotFound:
        raise Failure("parse_include: %s not found\n" % procedure)
    proc_name = ext_xml.attrib(include_xml, "name")
    prefix = lambda x: proc_name + "." + x
    reroutes = build_assocs("with", "from", "to", include_xml)
    args_assocs = build_assocs("arg", "name", "value", include_xml)
    try:
        proc = ext_xml.parse_file(f, noprovedtd=True)
        params = [x for x in X.children(proc) if ext_xml.tag_is(x, "param")]

        def make_assoc(xml):
            name = ext_xml.attrib(xml, "name")
            for a, v in args_assocs:
                if a == name:
                    return (name, v)
            try:
                return (name, X.attrib(xml, "default_value"))
            except Exception:
                raise Failure("Value required for param '%s' in %s" % (name, X.to_string(include_xml)))

        env_ = [make_assoc(p) for p in params]
        waypoints = get_children("waypoints", proc)
        exceptions = get_children("exceptions", proc)
        modules = get_children("modules", proc)
        blocks = get_children("blocks", proc)
        sectors = get_children("sectors", proc)
        header = get_pc_data("header", proc)
        variables = get_children("variables", proc)
        exceptions = [transform_exception(prefix, reroutes, (proc_name, env_), e) for e in exceptions]
        blocks = [transform_block(prefix, reroutes, (proc_name, env_), b) for b in blocks]
        acc = append_pc_data("header", header, flight_plan)
        for tc in reversed([("waypoints", waypoints), ("blocks", blocks), ("modules", modules),
                            ("variables", variables), ("exceptions", exceptions), ("sectors", sectors)]):
            acc = append_children(tc, acc)
        return acc
    except Failure as e:
        sys.stderr.write("Error: %s\n" % e)
        sys.stderr.flush()
        raise SystemExit(1)


def replace_children(xml, new_children_assoc):
    kids = []
    for x in X.children(xml):
        t = X.tag(x)
        for a, v in new_children_assoc:
            if a == t:
                kids.append(v)
                break
        else:
            kids.append(x)
    return X.Element(X.tag(xml), X.attribs(xml), kids)


def process_includes(dir_, xml):
    try:
        includes = X.children(ext_xml.child(xml, "includes"))
    except NotFound:
        includes = []
    acc = ext_xml.remove_child("includes", xml)
    for inc in includes:
        acc = parse_include(dir_, acc, inc)
    return acc


def remove_attribs(xml, names):
    return [(x, v) for x, v in X.attribs(xml) if x.lower() not in names]


def xml_assoc_attrib(a, v, xmls):
    for x in xmls:
        if ext_xml.attrib(x, a) == v:
            return x
    raise NotFound(v)


def new_waypoint(wp, qdr, dist, waypoints):
    wp_xml = xml_assoc_attrib("name", wp, waypoints[0])
    wp2d = (ext_xml.float_attrib(wp_xml, "x"), ext_xml.float_attrib(wp_xml, "y"))
    a = geo.deg_to_rad(90.0 - qdr)
    xy = geo.vect_add(wp2d, geo.polar2cart(dist, a))
    x = string_of_float(xy[0])
    y = string_of_float(xy[1])
    name = "%s_%.0f_%.0f" % (wp, qdr, dist)
    try:
        alt = [("alt", X.attrib(wp_xml, "alt"))]
    except Exception:
        alt = []
    waypoints[0] = [X.Element("waypoint", [("name", name), ("x", x), ("y", y)] + alt, [])] + waypoints[0]
    return name


def _replace(stage, waypoints, key):
    try:
        qdr = ext_xml.float_attrib(stage, key + "_qdr")
        dist = ext_xml.float_attrib(stage, key + "_dist")
        wp = ext_xml.attrib(stage, key)
        name = new_waypoint(wp, qdr, dist, waypoints)
        other = remove_attribs(stage, [key, key + "_qdr", key + "_dist"])
        return X.Element(X.tag(stage), [(key, name)] + other, [])
    except Exception:
        return stage


def process_stage(stage, waypoints):
    def do_it(stage):
        t = X.tag(stage).lower()
        if t in ("go", "stay", "circle"):
            return _replace(_replace(stage, waypoints, "wp"), waypoints, "from")
        if t == "while":
            return X.Element("while", X.attribs(stage), [do_it(c) for c in X.children(stage)])
        return stage
    return do_it(stage)


def process_relative_waypoints(xml):
    waypoints = ext_xml.child(xml, "waypoints")
    blocks = ext_xml.child(xml, "blocks")
    wl = [X.children(waypoints)]
    blocks_list = []
    for block in X.children(blocks):
        kids = [process_stage(s, wl) for s in X.children(block)]
        blocks_list.append(X.Element(X.tag(block), X.attribs(block), kids))
    new_waypoints = X.Element("waypoints", X.attribs(waypoints), wl[0])
    new_blocks = X.Element("blocks", X.attribs(blocks), blocks_list)
    return replace_children(xml, [("waypoints", new_waypoints), ("blocks", new_blocks)])


def _split_path(s):
    parts = re.split(r"[ \t,]+", s)
    while parts and parts[0] == "":
        parts.pop(0)
    while parts and parts[-1] == "":
        parts.pop()
    return parts


def stage_process_path(stage, rest):
    t = X.tag(stage)
    if t == "path":
        wpts = _split_path(ext_xml.attrib(stage, "wpts"))
        attribs = X.attribs(stage)

        def loop(ws):
            if not ws:
                raise Failure("Waypoint expected in path stage")
            if len(ws) == 1:
                return [X.Element("go", [("wp", ws[0])] + attribs, [])] + rest
            wp1, wp2, ps = ws[0], ws[1], ws[2:]
            head = X.Element("go", [("from", wp1), ("hmode", "route"), ("wp", wp2)] + attribs, [])
            return [head] + (rest if not ps else loop([wp2] + ps))

        return loop(wpts)
    if t in ("for", "while"):
        acc = []
        for c in reversed(X.children(stage)):
            acc = stage_process_path(c, acc)
        return [X.Element(t, X.attribs(stage), acc)] + rest
    return [stage] + rest


def block_process_path(block):
    acc = []
    for s in reversed(X.children(block)):
        acc = stage_process_path(s, acc)
    return X.Element(X.tag(block), X.attribs(block), acc)


def process_paths(xml):
    blocks = ext_xml.child(xml, "blocks")
    new_blocks = X.Element("blocks", X.attribs(blocks), [block_process_path(b) for b in X.children(blocks)])
    return replace_children(xml, [("blocks", new_blocks)])
