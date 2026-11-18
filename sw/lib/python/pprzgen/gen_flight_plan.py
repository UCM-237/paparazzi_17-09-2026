"""Port of sw/tools/generators/gen_flight_plan.ml: flight_plan.h and the
expanded flight_plan.xml dump."""

import os
import re
import sys

from . import xml_light as X
from . import ext_xml, env, geo
from . import expr as E
from . import fp_proc as FP
from .xml2h import define_out, define_string_out, finish_out, open_out
from .ocaml import Failure, NotFound, InvalidArgument, float_of_string, string_of_float

sof = string_of_float


class _State:
    """The OCaml module-level refs."""
    index_of_blocks = []
    check_expressions = False
    margin = 0
    stage = 0
    ground_alt = 0.0
    security_height = 0.0
    fp_wgs84 = geo.Geo(0.0, 0.0)
    label = 0  # gen_label counter: never reset (closure in OCaml)


S = _State


def reinit():
    S.index_of_blocks = []
    S.check_expressions = False
    S.margin = 0
    S.stage = 0


def georef_of_xml(xml):
    lat0 = geo.deg_of_string(ext_xml.attrib(xml, "lat0"))
    lon0 = geo.deg_of_string(ext_xml.attrib(xml, "lon0"))
    return geo.Geo(posn_lat=geo.deg_to_rad(lat0), posn_long=geo.deg_to_rad(lon0))


def parse(s):
    e = FP.parse_expression(s)
    if S.check_expressions:
        E.check_expression(e)
    return E.sprint(e, call_assoc=("IndexOfBlock", S.index_of_blocks))


def parsed_attrib(xml, a):
    return parse(ext_xml.attrib(xml, a))


def gen_label(p):
    S.label += 1
    return "%s_%d" % (p, S.label)


def right():
    S.margin += 2


def left():
    S.margin -= 2


def lprintf(out, s):
    out.write(" " * S.margin + s)


def float_attrib(xml, a):
    v = X.attrib(xml, a)
    try:
        return float_of_string(v)
    except Failure:
        raise Failure("Float expected in attribute '%s' from %s" % (a, X.to_string_fmt(xml)))


def name_of(wp):
    return ext_xml.attrib(wp, "name")


def _err(s):
    sys.stderr.write(s)
    sys.stderr.flush()


def check_altitude_srtm(a, x, wgs84):
    try:
        srtm_alt = float(geo.srtm_of_wgs84(wgs84))
        if a < srtm_alt:
            _err("MAJOR NOTICE: below SRTM ground altitude (%.0f<%.0f) in %s\n" % (a, srtm_alt, X.to_string(x)))
    except geo.TileNotFound:
        _err("No SRTM data found to check altitude.\n")


def check_altitude(a, x):
    if a < S.ground_alt + S.security_height:
        _err("NOTICE: low altitude (%.0f<%.0f+%.0f) in %s\n" % (a, S.ground_alt, S.security_height, X.to_string(x)))


def localize_waypoint(rel_utm_of_wgs84, waypoint):
    try:
        # OCaml evaluates function arguments right to left
        lon = geo.deg_of_string(X.attrib(waypoint, "lon"))
        lat = geo.deg_of_string(X.attrib(waypoint, "lat"))
    except X.NoAttribute as e:
        if str(e) in ("lat", "lon"):
            return waypoint
        raise
    x, y = rel_utm_of_wgs84(geo.make_geo_deg(lat, lon))
    xs = "%.2f" % x
    ys = "%.2f" % y
    return ext_xml.subst_attrib("y", ys, ext_xml.subst_attrib("x", xs, waypoint))


class Ref0:
    def __init__(self, frame, utm0, ecef0, geo0):
        self.frame, self.utm0, self.ecef0, self.geo0 = frame, utm0, ecef0, geo0


def wgs84_of_x_y(ref0, x, y, alt):
    if ref0.frame == "UTM":
        return geo.of_utm(geo.utm_add(ref0.utm0, (x, y)))
    ned = [y, x, -(float_of_string(alt) - S.ground_alt)]
    ecef = geo.ecef_of_ned(ref0.ecef0, ned)
    g, _ = geo.geo_of_ecef(ecef)
    return g


def _xy_alt(waypoint, default_alt):
    y = float_attrib(waypoint, "y")  # tuple evaluated right to left
    x = float_attrib(waypoint, "x")
    try:
        alt = sof(float_attrib(waypoint, "height") + S.ground_alt)
    except Exception:
        alt = default_alt
    try:
        alt = X.attrib(waypoint, "alt")
    except Exception:
        pass
    return x, y, alt


def print_waypoint_utm(out, ref0, default_alt, waypoint):
    x, y, alt = _xy_alt(waypoint, default_alt)
    check_altitude(float_of_string(alt), waypoint)
    if ref0.frame == "LTP":
        wgs84 = wgs84_of_x_y(ref0, x, y, alt)
        utm = geo.utm_of(wgs84, zone=ref0.utm0.utm_zone)
        x, y = utm.utm_x - ref0.utm0.utm_x, utm.utm_y - ref0.utm0.utm_y
    out.write(" {%.1f, %.1f, %s},\\\n" % (x, y, alt))


def print_waypoint_enu(out, ref0, default_alt, waypoint):
    x, y, alt = _xy_alt(waypoint, default_alt)
    if ref0.frame == "UTM":
        ecef = geo.ecef_of_geo(geo.of_utm(geo.utm_add(ref0.utm0, (x, y))), float_of_string(alt))
        ned = geo.ned_of_ecef(ref0.ecef0, ecef)
    else:
        ned = [y, x, -(float_of_string(alt) - S.ground_alt)]
    out.write(" {%.2f, %.2f, %.2f}, /* ENU in meters  */ \\\n" % (ned[1], ned[0], -ned[2]))


def convert_angle(rad):
    return geo.int64_of_float(1e7 * geo.rad_to_deg(rad))


def print_waypoint_lla(out, ref0, default_alt, waypoint):
    x, y, alt = _xy_alt(waypoint, default_alt)
    wgs84 = wgs84_of_x_y(ref0, x, y, alt)
    # printf arguments are evaluated right to left
    msl = geo.egm96_of_wgs84(wgs84)
    a = 1000.0 * float_of_string(alt)
    out.write(" {.lat=%d, .lon=%d, .alt=%.0f}, /* 1e7deg, 1e7deg, mm (above NAV_MSL0, local msl=%.2fm) */ \\\n"
              % (convert_angle(wgs84.posn_lat), convert_angle(wgs84.posn_long), a, msl))


def print_waypoint_lla_wgs84(out, ref0, default_alt, waypoint):
    x, y, alt = _xy_alt(waypoint, default_alt)
    wgs84 = wgs84_of_x_y(ref0, x, y, alt)
    if geo.srtm_available(wgs84):
        check_altitude_srtm(float_of_string(alt), waypoint, wgs84)
    altf = float_of_string(alt) + geo.egm96_of_wgs84(wgs84)
    out.write(" {.lat=%d, .lon=%d, .alt=%.0f}, /* 1e7deg, 1e7deg, mm (above WGS84 ref ellipsoid) */ \\\n"
              % (convert_angle(wgs84.posn_lat), convert_angle(wgs84.posn_long), 1000.0 * altf))


def print_waypoint_global(out, waypoint):
    try:
        float_attrib(waypoint, "lon")
        float_attrib(waypoint, "lat")
        out.write(" TRUE, \\\n")
    except Exception:
        out.write(" FALSE, \\\n")


def get_index_block(x):
    for a, v in S.index_of_blocks:
        if a == x:
            return v
    raise Failure("Unknown block: '%s'" % x)


def print_exception(out, x):
    c = parsed_attrib(x, "cond")
    i = get_index_block(ext_xml.attrib(x, "deroute"))
    try:
        f = ext_xml.attrib(x, "exec")
    except ext_xml.ExtXmlError:
        lprintf(out, "if ((nav_block != %d) && %s) { GotoBlock(%d); return; }\n" % (i, c, i))
    else:
        lprintf(out, "if ((nav_block != %d) && %s) {%s; GotoBlock(%d); return; }\n" % (i, c, f, i))


def element(a, b, c):
    return X.Element(a, b, c)


def goto(l):
    return element("goto", [("name", l)], [])


EXIT_BLOCK = element("exit_block", [], [])
HOME_BLOCK = X.parse_string('<block name="HOME"><home/></block>')


def output_label(out, l):
    lprintf(out, "Label(%s)\n" % l)


def get_index_waypoint(x, l):
    for a, v in l:
        if a == x:
            return str(v)
    raise Failure("Unknown waypoint: %s" % x)


def pprz_throttle(s):
    # the range check raises Failure inside a `with Failure _ -> ()`: no-op
    return "9600*(%s)" % s


def output_vmode(out, stage_xml, wp, last_wp):
    try:
        pitch = X.attrib(stage_xml, "pitch")
    except Exception:
        pitch = "0.0"
    t = ext_xml.attrib_or_default(stage_xml, "nav_type", "Nav")
    if pitch == "auto":
        lprintf(out, "%sVerticalAutoPitchMode(%s);\n" % (t, pprz_throttle(parsed_attrib(stage_xml, "throttle"))))
    else:
        lprintf(out, "%sVerticalAutoThrottleMode(RadOfDeg(%s));\n" % (t, parse(pitch)))
    try:
        vmode = ext_xml.attrib(stage_xml, "vmode")
    except Exception:
        vmode = "alt"
    if vmode == "climb":
        lprintf(out, "%sVerticalClimbMode(%s);\n" % (t, parsed_attrib(stage_xml, "climb")))
    elif vmode == "alt":
        try:
            a = parsed_attrib(stage_xml, "alt")
            try:
                check_altitude(float_of_string(a), stage_xml)
            except Failure:
                pass
            alt = a
        except Exception:
            try:
                h = parsed_attrib(stage_xml, "height")
                try:
                    check_altitude(float_of_string(h) + S.ground_alt, stage_xml)
                except Failure:
                    pass
                alt = "Height(%s)" % h
            except Exception:
                if wp == "":
                    raise Failure("alt or waypoint required in alt vmode")
                alt = "WaypointAlt(%s)" % wp
        lprintf(out, "%sVerticalAltitudeMode(%s, 0.);\n" % (t, alt))
    elif vmode == "xyz":
        pass
    elif vmode == "glide":
        lprintf(out, "%sGlide(%s, %s);\n" % (t, last_wp, wp))
    elif vmode == "throttle":
        if pitch == "auto":
            raise Failure("auto pich mode not compatible with vmode=throttle")
        lprintf(out, "%sVerticalThrottleMode(%s);\n" % (t, pprz_throttle(parsed_attrib(stage_xml, "throttle"))))
    else:
        raise Failure("Unknown vmode '%s'" % vmode)
    return vmode


def _nav_params(x):
    try:
        return ", " + X.attrib(x, "nav_params")
    except Exception:
        return ""


def output_hmode(out, x, wp, last_wp):
    t = ext_xml.attrib_or_default(x, "nav_type", "Nav")
    p = _nav_params(x)
    try:
        hmode = ext_xml.attrib(x, "hmode")
    except ext_xml.ExtXmlError:
        lprintf(out, "%sGotoWaypoint(%s%s);\n" % (t, wp, p))
        return "direct"
    if hmode == "route":
        if last_wp == "last_wp":
            _err("NOTICE: Deprecated use of 'route' using last waypoint in %s\n" % X.to_string(x))
        lprintf(out, "%sSegment(%s, %s%s);\n" % (t, last_wp, wp, p))
    elif hmode == "direct":
        lprintf(out, "%sGotoWaypoint(%s%s);\n" % (t, wp, p))
    else:
        raise Failure("Unknown hmode '%s'" % hmode)
    return hmode


_LEAF_STAGES = ("return", "goto", "deroute", "exit_block", "follow", "call", "call_once", "home",
                "heading", "attitude", "go", "stay", "xyz", "set", "circle", "guided")


def index_stage(x):
    t = X.tag(x)
    if t == "for":
        S.stage += 1
        S.stage += 1
        n = S.stage
        l = [index_stage(c) for c in X.children(x)]
        S.stage += 1
        return X.Element(t, X.attribs(x) + [("no", str(n))], l)
    if t == "while":
        S.stage += 1
        n = S.stage
        l = [index_stage(c) for c in X.children(x)]
        S.stage += 1
        return X.Element(t, X.attribs(x) + [("no", str(n))], l)
    if t in _LEAF_STAGES:
        S.stage += 1
        return X.Element(t, X.attribs(x) + [("no", str(S.stage))], X.children(x))
    if t in ("survey_rectangle", "eight", "oval"):
        S.stage += 1
        S.stage += 1
        return X.Element(t, X.attribs(x) + [("no", str(S.stage))], X.children(x))
    if t == "exception":
        return x
    raise Failure("Unknown stage: %s\n" % t)


def inside_function(name):
    return "Inside" + (name[:1].upper() + name[1:] if name else name)


def fp_pre_call(out, x):
    try:
        lprintf(out, "%s;\n" % ext_xml.attrib(x, "pre_call"))
    except Exception:
        pass


def fp_post_call(out, x):
    try:
        lprintf(out, "%s;\n" % ext_xml.attrib(x, "post_call"))
    except Exception:
        pass


def output_speed(out, x, t):
    lprintf(out, "%sSetMaxSpeed(%s);\n" % (t, ext_xml.attrib_or_default(x, "max_speed", "-1.")))


def stage_until(out, x):
    try:
        cond = parsed_attrib(x, "until")
    except ext_xml.ExtXmlError:
        return
    lprintf(out, "if (%s) {\n" % cond)
    right()
    fp_post_call(out, x)
    lprintf(out, "NextStageAndBreak()\n")
    left()
    lprintf(out, "}\n")


def print_stage(out, idx_wp, x):
    def stage():
        S.stage += 1
        lprintf(out, "Stage(%d)\n" % S.stage)
        right()

    t = X.tag(x).lower()
    if t == "return":
        stage()
        lprintf(out, "Return(%s);\n" % ext_xml.attrib_or_default(x, "reset_stage", "0"))
        lprintf(out, "break;\n")
    elif t == "goto":
        stage()
        lprintf(out, "Goto(%s)\n" % name_of(x))
    elif t == "deroute":
        stage()
        lprintf(out, "GotoBlock(%d);\n" % get_index_block(ext_xml.attrib(x, "block")))
        lprintf(out, "break;\n")
    elif t == "exit_block":
        lprintf(out, "/* Falls through. */\n")
        lprintf(out, "default:\n")
        stage()
        lprintf(out, "NextBlock();\n")
        lprintf(out, "break;\n")
    elif t == "while":
        w = gen_label("while")
        e = gen_label("endwhile")
        output_label(out, w)
        stage()
        try:
            c = parsed_attrib(x, "cond")
        except Exception:
            c = "TRUE"
        lprintf(out, "if (! (%s)) Goto(%s) else NextStageAndBreak();\n" % (c, e))
        for ch in X.children(x):
            print_stage(out, idx_wp, ch)
        print_stage(out, idx_wp, goto(w))
        output_label(out, e)
    elif t == "for":
        f = gen_label("for")
        e = gen_label("endfor")
        v = E.c_var_of_ident(ext_xml.attrib(x, "var"))
        from_ = parsed_attrib(x, "from")
        to_expr = parsed_attrib(x, "to")
        to_var = v + "_to"
        lprintf(out, "static int8_t %s;\n" % v)
        lprintf(out, "static int8_t %s;\n" % to_var)
        stage()
        lprintf(out, "%s = %s - 1;\n" % (v, from_))
        lprintf(out, "%s = %s;\n" % (to_var, to_expr))
        lprintf(out, "INTENTIONAL_FALLTHRU\n")
        left()
        output_label(out, f)
        stage()
        lprintf(out, "if (++%s > %s) Goto(%s) else NextStageAndBreak();\n" % (v, to_var, e))
        for ch in X.children(x):
            print_stage(out, idx_wp, ch)
        print_stage(out, idx_wp, goto(f))
        output_label(out, e)
    elif t == "heading":
        stage()
        fp_pre_call(out, x)
        tt = ext_xml.attrib_or_default(x, "nav_type", "Nav")
        p = _nav_params(x)
        output_speed(out, x, tt)
        lprintf(out, "%sHeading(RadOfDeg(%s)%s);\n" % (tt, parsed_attrib(x, "course"), p))
        output_vmode(out, x, "", "")
        stage_until(out, x)
        fp_post_call(out, x)
        lprintf(out, "break;\n")
    elif t == "follow":
        stage()
        fp_pre_call(out, x)
        id_ = ext_xml.attrib(x, "ac_id")
        d = ext_xml.attrib(x, "distance")
        h = ext_xml.attrib(x, "height")
        tt = ext_xml.attrib_or_default(x, "nav_type", "Nav")
        p = _nav_params(x)
        lprintf(out, "%sFollow(%s, %s, %s%s);\n" % (tt, id_, d, h, p))
        fp_post_call(out, x)
        lprintf(out, "break;\n")
    elif t == "attitude":
        stage()
        fp_pre_call(out, x)
        tt = ext_xml.attrib_or_default(x, "nav_type", "Nav")
        p = _nav_params(x)
        lprintf(out, "%sAttitude(RadOfDeg(%s)%s);\n" % (tt, parsed_attrib(x, "roll"), p))
        output_vmode(out, x, "", "")
        stage_until(out, x)
        fp_post_call(out, x)
        lprintf(out, "break;\n")
    elif t == "go":
        stage()
        fp_pre_call(out, x)
        tt = ext_xml.attrib_or_default(x, "nav_type", "Nav")
        try:
            wp = get_index_waypoint(ext_xml.attrib(x, "wp"), idx_wp)
        except ext_xml.ExtXmlError:
            lprintf(out, "waypoints[0].x = %s;\n" % parsed_attrib(x, "x"))
            lprintf(out, "waypoints[0].y = %s;\n" % parsed_attrib(x, "y"))
            wp = "0"
        try:
            at = ext_xml.attrib(x, "approaching_time")
        except Exception:
            at = None
        try:
            et = ext_xml.attrib(x, "exceeding_time")
        except Exception:
            et = None
        if at is not None and et is None:
            pass
        elif at is None and et is not None:
            at = "-" + et
        elif at is None and et is None:
            at = "CARROT"
        else:
            raise Failure("Error: 'approaching_time' and 'exceeding_time' attributes are not compatible")
        try:
            last_wp = get_index_waypoint(ext_xml.attrib(x, "from"), idx_wp)
        except ext_xml.ExtXmlError:
            last_wp = "last_wp"
        if last_wp == "last_wp":
            lprintf(out, "if (%sApproaching(%s,%s)) {\n" % (tt, wp, at))
        else:
            lprintf(out, "if (%sApproachingFrom(%s,%s,%s)) {\n" % (tt, wp, last_wp, at))
        right()
        fp_post_call(out, x)
        lprintf(out, "NextStageAndBreakFrom(%s);\n" % wp)
        left()
        lprintf(out, "} else {\n")
        right()
        output_speed(out, x, tt)
        hmode = output_hmode(out, x, wp, last_wp)
        vmode = output_vmode(out, x, wp, last_wp)
        if vmode == "glide" and hmode != "route":
            raise Failure("glide vmode requires route hmode")
        left()
        lprintf(out, "}\n")
        stage_until(out, x)
        fp_post_call(out, x)
        lprintf(out, "break;\n")
    elif t == "stay":
        stage()
        fp_pre_call(out, x)
        tt = ext_xml.attrib_or_default(x, "nav_type", "Nav")
        p = _nav_params(x)
        output_speed(out, x, tt)
        # the OCaml handler catches Xml2h.Error, which is never raised
        wp = get_index_waypoint(ext_xml.attrib(x, "wp"), idx_wp)
        output_hmode(out, x, wp, "")
        output_vmode(out, x, wp, "")
        stage_until(out, x)
        fp_post_call(out, x)
        lprintf(out, "break;\n")
    elif t == "xyz":
        stage()
        fp_pre_call(out, x)
        try:
            r = parsed_attrib(x, "radius")
        except Exception:
            r = "100"
        lprintf(out, "Goto3D(%s)\n" % r)
        x = ext_xml.subst_attrib("vmode", "xyz", x)
        output_vmode(out, x, "", "")
        fp_post_call(out, x)
        lprintf(out, "break;\n")
    elif t == "home":
        stage()
        lprintf(out, "nav_home();\n")
        lprintf(out, "break;\n")
    elif t == "circle":
        stage()
        fp_pre_call(out, x)
        wp = get_index_waypoint(ext_xml.attrib(x, "wp"), idx_wp)
        r = parsed_attrib(x, "radius")
        output_vmode(out, x, wp, "")
        tt = ext_xml.attrib_or_default(x, "nav_type", "Nav")
        p = _nav_params(x)
        output_speed(out, x, tt)
        lprintf(out, "%sCircleWaypoint(%s, %s%s);\n" % (tt, wp, r, p))
        stage_until(out, x)
        fp_post_call(out, x)
        lprintf(out, "break;\n")
    elif t == "guided":
        stage()
        fp_pre_call(out, x)
        cmds = ext_xml.attrib(x, "commands")
        flags = ext_xml.attrib_or_default(x, "flags", "0")
        tt = ext_xml.attrib_or_default(x, "nav_type", "Nav")
        p = _nav_params(x)
        output_speed(out, x, tt)
        lprintf(out, "%sGuided(%s, %s%s);\n" % (tt, flags, cmds, p))
        stage_until(out, x)
        fp_post_call(out, x)
        lprintf(out, "break;\n")
    elif t in ("eight", "oval"):
        stage()
        lprintf(out, "nav_%s_init();\n" % t)
        lprintf(out, "NextStageAndBreak();\n")
        left()
        stage()
        fp_pre_call(out, x)
        if t == "eight":
            a1 = get_index_waypoint(ext_xml.attrib(x, "center"), idx_wp)
            a2 = get_index_waypoint(ext_xml.attrib(x, "turn_around"), idx_wp)
        else:
            a1 = get_index_waypoint(ext_xml.attrib(x, "p1"), idx_wp)
            a2 = get_index_waypoint(ext_xml.attrib(x, "p2"), idx_wp)
        r = parsed_attrib(x, "radius")
        output_speed(out, x, "Nav")
        output_vmode(out, x, a1, "")
        lprintf(out, "%s(%s, %s, %s);\n" % ("Eight" if t == "eight" else "Oval", a1, a2, r))
        stage_until(out, x)
        fp_post_call(out, x)
        lprintf(out, "break;\n")
    elif t == "set":
        stage()
        var = ext_xml.attrib(x, "var")
        value = parsed_attrib(x, "value")
        lprintf(out, "%s = %s;\n" % (var, value))
        lprintf(out, "NextStage();\n")
    elif t == "call":
        stage()
        statement = ext_xml.attrib(x, "fun")
        loop = ext_xml.attrib_or_default(x, "loop", "TRUE").upper()
        brk = ext_xml.attrib_or_default(x, "break", "FALSE").upper()
        if loop == "TRUE":
            lprintf(out, "if (! (%s)) {\n" % statement)
            if brk == "TRUE":
                lprintf(out, "  NextStageAndBreak();\n")
            elif brk == "FALSE":
                lprintf(out, "  NextStage();\n")
            else:
                raise Failure("FP: 'call' break attribute must be TRUE or FALSE")
            lprintf(out, "} else {\n")
            try:
                c = parsed_attrib(x, "until")
                lprintf(out, "  if (%s) NextStageAndBreak();\n" % c)
            except ext_xml.ExtXmlError:
                pass
            lprintf(out, "  break;\n")
            lprintf(out, "}\n")
        elif loop == "FALSE":
            lprintf(out, "%s;\n" % statement)
            if brk == "TRUE":
                lprintf(out, "NextStageAndBreak();\n")
            elif brk == "FALSE":
                lprintf(out, "NextStage();\n")
            else:
                raise Failure("FP: 'call' break attribute must be TRUE or FALSE")
        else:
            raise Failure("FP: 'call' loop attribute must be TRUE or FALSE")
    elif t == "call_once":
        stage()
        statement = ext_xml.attrib(x, "fun")
        brk = ext_xml.attrib_or_default(x, "break", "FALSE").upper()
        lprintf(out, "%s;\n" % statement)
        if brk == "TRUE":
            lprintf(out, "NextStageAndBreak();\n")
        elif brk == "FALSE":
            lprintf(out, "NextStage();\n")
        else:
            raise Failure("FP: 'call_once' break attribute must be TRUE or FALSE")
    elif t == "survey_rectangle":
        grid = parsed_attrib(x, "grid")
        wp1 = get_index_waypoint(ext_xml.attrib(x, "wp1"), idx_wp)
        wp2 = get_index_waypoint(ext_xml.attrib(x, "wp2"), idx_wp)
        orientation = ext_xml.attrib_or_default(x, "orientation", "NS")
        tt = ext_xml.attrib_or_default(x, "nav_type", "Nav")
        p = _nav_params(x)
        stage()
        output_speed(out, x, tt)
        if orientation != "NS" and orientation != "WE":
            raise Failure("Unknown survey orientation (NS or WE): %s" % orientation)
        lprintf(out, "%sSurveyRectangleInit(%s, %s, %s, %s%s);\n" % (tt, wp1, wp2, grid, orientation, p))
        lprintf(out, "NextStageAndBreak();\n")
        left()
        stage()
        fp_pre_call(out, x)
        lprintf(out, "%sSurveyRectangle(%s, %s);\n" % (tt, wp1, wp2))
        stage_until(out, x)
        fp_post_call(out, x)
        lprintf(out, "break;\n")
    else:
        raise Failure("Unreachable")
    left()


def indexed_stages(blocks):
    lstages = []
    for b in blocks:
        block_name = name_of(b)
        block_no = ext_xml.attrib(b, "no")

        def f(stage):
            try:
                stage_no = X.attrib(stage, "no")
            except X.NoAttribute as e:
                if str(e) != "no":
                    raise
                if not ext_xml.tag_is(stage, "exception"):
                    raise AssertionError("gen_flight_plan.ml: indexed_stages")
                return
            lstages.insert(0, X.Element("stage", [("block", block_no), ("block_name", block_name),
                                                  ("stage", stage_no)], [stage]))
            if ext_xml.tag_is(stage, "for") or ext_xml.tag_is(stage, "while"):
                for c in X.children(stage):
                    f(c)

        for c in X.children(b):
            f(c)
    return lstages


def index_blocks(xml):
    block = -1
    out = []
    for b in X.children(xml):
        block += 1
        name = name_of(b)
        if any(n == name for n, _ in S.index_of_blocks):
            raise Failure("Error in flight plan: Block '%s' defined twice" % name)
        S.index_of_blocks = [(name, block)] + S.index_of_blocks
        S.stage = -1
        stages = [index_stage(c) for c in X.children(b)]
        out.append(X.Element(X.tag(b), X.attribs(b) + [("no", str(block))], stages))
    return X.Element(X.tag(xml), X.attribs(xml), out)


def print_block(out, idx_wp, b, block_num):
    n = name_of(b)
    lprintf(out, "Block(%d) // %s\n" % (block_num, n))
    fp_pre_call(out, b)
    excpts = [x for x in X.children(b) if X.tag(x) == "exception"]
    stages = [x for x in X.children(b) if X.tag(x) != "exception"]
    for e in excpts:
        print_exception(out, e)
    lprintf(out, "switch(nav_stage) {\n")
    right()
    S.stage = -1
    for s in stages:
        print_stage(out, idx_wp, s)
    print_stage(out, idx_wp, EXIT_BLOCK)
    left()
    lprintf(out, "}\n")
    fp_post_call(out, b)
    lprintf(out, "break;\n\n")


def print_blocks(out, idx_wp, bs):
    for i, b in enumerate(bs):
        print_block(out, idx_wp, b, i)


def c_suffix(s):
    return re.match(r"[a-zA-Z0-9_]*(\n|\Z)", s) is not None


def define_waypoints_indices(out, wpts):
    for i, w in enumerate(wpts):
        n = name_of(w)
        if c_suffix(n):
            define_out(out, "WP_%s" % n, str(i))


def home(waypoints):
    for w in waypoints:
        if name_of(w) == "HOME":
            y = float_attrib(w, "y")
            x = float_attrib(w, "x")
            return (x, y)
    raise Failure("Waypoint 'HOME' required")


def check_distance(h, max_d, wp):
    hx, hy = h
    x = float_attrib(wp, "x")
    y = float_attrib(wp, "y")
    from .ocaml import cm
    d = cm.sqrt(cm.pow(x - hx, 2.0) + cm.pow(y - hy, 2.0))
    if d > max_d:
        _err("\nWarning: Waypoint '%s' too far from HOME (%.0f>%.0f)\n\n" % (name_of(wp), d, max_d))


def check_geo_ref(wgs84, xml):
    utm0 = geo.utm_of(wgs84)
    v = float_attrib(xml, "max_dist_from_home")
    max_d = 1000.0 if 1000.0 <= v else v

    def check_zone(u):
        if geo.utm_of(geo.of_utm(u)).utm_zone != utm0.utm_zone:
            sys.stderr.write("Warning: You are close (less than twice the max distance) to an UTM zone border ! "
                             "The navigation will not work unless the GPS receiver configured to send the POSLLH message.\n")
            sys.stderr.flush()

    check_zone(utm0._replace(utm_x=utm0.utm_x + 2.0 * max_d))
    check_zone(utm0._replace(utm_x=utm0.utm_x - 2.0 * max_d))
    wpts = ext_xml.child(xml, "waypoints")
    wpts = ext_xml.subst_attrib("utm_x0", sof(utm0.utm_x), wpts)
    wpts = ext_xml.subst_attrib("utm_y0", sof(utm0.utm_y), wpts)
    return ext_xml.subst_child("waypoints", wpts, xml)


DUMMY_WAYPOINT = X.Element("waypoint", [("name", "dummy"), ("x", "0."), ("y", "0.")], [])


def print_inside_polygon_global(out, pts, name):
    lprintf(out, "uint8_t i, j;\n")
    lprintf(out, "bool c = false;\n")
    lprintf(out, "const uint8_t nb_pts = %s_NB;\n" % name)
    lprintf(out, "const uint8_t wps_id[] = %s;\n\n" % name)
    lprintf(out, "for (i = 0, j = nb_pts - 1; i < nb_pts; j = i++) {\n")
    right()
    lprintf(out, "if (((WaypointY(wps_id[i]) > _y) != (WaypointY(wps_id[j]) > _y)) &&\n")
    lprintf(out, "   (_x < (WaypointX(wps_id[j])-WaypointX(wps_id[i])) * (_y-WaypointY(wps_id[i])) / (WaypointY(wps_id[j])-WaypointY(wps_id[i])) + WaypointX(wps_id[i]))) {\n")
    right()
    lprintf(out, "if (c == TRUE) { c = FALSE; } else { c = TRUE; }\n")
    left()
    lprintf(out, "}\n")
    left()
    lprintf(out, "}\n")
    lprintf(out, "return c;\n")


def print_inside_sector(out, sector):
    s, pts = sector
    ids = [i for i, _ in pts]
    name = "SECTOR_" + s.upper()
    define_out(out, name + "_NB", str(len(pts)))
    define_out(out, name, "{ " + ", ".join(ids) + " }")
    lprintf(out, "static inline bool %s(float _x, float _y) {\n" % inside_function(s))
    right()
    print_inside_polygon_global(out, pts, name)
    left()
    lprintf(out, "}\n\n")


def parse_wpt_sector(indexes, waypoints, xml):
    sector_name = ext_xml.attrib(xml, "name")

    def p2d_of(x):
        name = name_of(x)
        wp = None
        for w in waypoints:
            if name_of(w) == name:
                wp = w
                break
        if wp is None:
            raise Failure("Error: corner '%s' of sector '%s' not found" % (name, sector_name))
        i = get_index_waypoint(name, indexes)
        xx = float_attrib(wp, "x")
        yy = float_attrib(wp, "y")
        return (i, (xx, yy))

    return (sector_name, [p2d_of(c) for c in X.children(xml)])


def extract_abi_msg(filename, class_):
    xml = ext_xml.parse_file(filename)
    try:
        xml_class = ext_xml.child(xml, "msg_class", select=lambda x: X.attrib(x, "name") == class_)
    except NotFound:
        raise Failure("No msg_class '%s' found" % class_)
    t = {}
    for x in X.children(xml_class):
        name = ext_xml.attrib(x, "name")
        t[name] = [ext_xml.attrib(f, "type") for f in X.children(x)]
    return t


def _split_vars(x):
    parts = re.split(r"[ ]*,[ ]*", x)
    while parts and parts[0] == "":
        parts.pop(0)
    while parts and parts[-1] == "":
        parts.pop()
    return parts


def parse_variables(xml):
    def some_or_none(v, n, cb):
        try:
            return cb(ext_xml.attrib(v, n))
        except Exception:
            return None

    res = []
    for var in xml:
        t = X.tag(var)
        if t == "variable":
            res.append(("var", ext_xml.attrib(var, "var"),
                        ext_xml.attrib_or_default(var, "type", "float"),
                        ext_xml.attrib_or_default(var, "init", "0")))
        elif t == "abi_binding":
            n = ext_xml.attrib(var, "name")
            vs = some_or_none(var, "vars", _split_vars)
            i = ext_xml.attrib_or_default(var, "id", "ABI_BROADCAST")
            h = some_or_none(var, "handler", lambda x: x)
            if (vs is None) == (h is None):
                raise Failure("Gen_flight_plan: either 'vars' or 'handler' should be defined, not both")
            res.append(("binding", n, vs, i, h))
        else:
            raise Failure("Gen_flight_plan: unexpected variables tag")
    return res


def _find_abi(abi_msgs, n):
    if n not in abi_msgs:
        raise NotFound(n)
    return abi_msgs[n]


def print_var_decl(out, v):
    if v[0] == "var":
        lprintf(out, "extern %s %s;\n" % (v[2], v[1]))


def print_var_impl(out, abi_msgs, v):
    if v[0] == "var":
        lprintf(out, "%s %s = %s;\n" % (v[2], v[1], v[3]))
        return
    _, n, vs, _i, h = v
    if vs is not None and h is None:
        lprintf(out, "static abi_event FP_%s_ev;\n" % n)
        field_types = _find_abi(abi_msgs, n)
        for k in range(min(len(field_types), len(vs))):
            if vs[k] != "_":
                lprintf(out, "static %s %s;\n" % (field_types[k], vs[k]))
        if len(field_types) != len(vs):
            raise InvalidArgument("List.iter2")
    elif vs is None and h is not None:
        lprintf(out, "static abi_event FP_%s_ev;\n" % n)


def print_auto_init_bindings(out, abi_msgs, variables):
    for v in variables:
        if v[0] == "binding" and v[2] is not None and v[4] is None:
            _, n, vs, _i, _h = v
            field_types = _find_abi(abi_msgs, n)
            lprintf(out, "static void FP_%s_cb(uint8_t sender_id __attribute__((unused))" % n)
            for i, vv in enumerate(vs):
                if i >= len(field_types):
                    raise Failure("nth")
                if vv == "_":
                    lprintf(out, ", %s _unused_%d __attribute__((unused))" % (field_types[i], i))
                else:
                    lprintf(out, ", %s _%s" % (field_types[i], vv))
            lprintf(out, ") {\n")
            for vv in vs:
                if vv != "_":
                    lprintf(out, "  %s = _%s;\n" % (vv, vv))
            lprintf(out, "}\n\n")
    lprintf(out, "static inline void auto_nav_init(void) {\n")
    for v in variables:
        if v[0] == "binding":
            _, n, _vs, i, h = v
            if h is None:
                lprintf(out, "  AbiBindMsg%s(%s, &FP_%s_ev, FP_%s_cb);\n" % (n, i, n, n))
            else:
                lprintf(out, "  AbiBindMsg%s(%s, &FP_%s_ev, %s);\n" % (n, i, n, h))
    lprintf(out, "}\n\n")


def print_enter_exit_functions(out, blocks):
    for name in ("on_enter", "on_exit"):
        lprintf(out, "void nav_%s_block(uint8_t block_id) {\n" % name)
        right()
        lprintf(out, "switch (block_id) {\n")
        right()
        for block_id, b in enumerate(blocks):
            try:
                f_name = ext_xml.attrib(b, name)
                lprintf(out, "case %d: // %s\n" % (block_id, name_of(b)))
                lprintf(out, "  %s;\n" % f_name)
                lprintf(out, "  break;\n")
            except Exception:
                pass
        lprintf(out, "default:\n")
        lprintf(out, "  break;\n")
        left()
        lprintf(out, "}\n")
        left()
        lprintf(out, "}\n\n")


def _ocaml_max(a, b):
    return a if a >= b else b


def print_flight_plan_h(xml, ref0, xml_file, out_file):
    with open_out(out_file) as out:
        waypoints = X.children(ext_xml.child(xml, "waypoints"))
        try:
            variables = X.children(ext_xml.child(xml, "variables"))
        except Exception:
            variables = []
        blocks = X.children(ext_xml.child(xml, "blocks"))
        try:
            global_exceptions = X.children(ext_xml.child(xml, "exceptions"))
        except Exception:
            global_exceptions = []

        h_name = "FLIGHT_PLAN_H"
        out.write("/* This file has been generated by gen_flight_plan from %s */\n" % xml_file)
        out.write("/* Version %s */\n" % env.get_paparazzi_version())
        out.write("/* Please DO NOT EDIT */\n\n")
        out.write("#ifndef %s\n" % h_name)
        define_out(out, h_name, "")
        out.write("\n")
        out.write('#include "std.h"\n')
        out.write('#include "generated/modules.h"\n')
        out.write('#include "modules/core/abi.h"\n')
        out.write('#include "autopilot.h"\n\n')

        variables = parse_variables(variables)
        abi_msgs = extract_abi_msg(env.paparazzi_home + "/conf/abi.xml", "airborne")
        for v in variables:
            print_var_decl(out, v)
        out.write("\n")
        try:
            header = ext_xml.child(ext_xml.child(xml, "header"), "0")
            out.write("%s\n\n" % X.pcdata(header))
        except Exception:
            pass

        name = ext_xml.attrib(xml, "name")
        define_string_out(out, "FLIGHT_PLAN_NAME", name)

        get_float = lambda a: float_attrib(xml, a)
        try:
            qfu = get_float("qfu")
        except X.NoAttribute as e:
            if str(e) != "qfu":
                raise
            qfu = 0.0
        mdfh = get_float("max_dist_from_home")
        alt = ext_xml.attrib(xml, "alt")
        S.security_height = get_float("security_height")

        if S.security_height < 0.0:
            _err("\nError: Security height cannot be negative (%.0f)\n" % S.security_height)
            raise SystemExit(1)
        try:
            home_mode_height = _ocaml_max(get_float("home_mode_height"), S.security_height)
        except Exception:
            home_mode_height = S.security_height
        check_altitude(float_of_string(alt), xml)
        check_altitude_srtm(float_of_string(alt), xml, S.fp_wgs84)

        define_out(out, "NAV_DEFAULT_ALT", "%.0f /* nominal altitude of the flight plan */" % float_of_string(alt))
        define_out(out, "NAV_UTM_EAST0", "%.0f" % ref0.utm0.utm_x)
        define_out(out, "NAV_UTM_NORTH0", "%.0f" % ref0.utm0.utm_y)
        define_out(out, "NAV_UTM_ZONE0", "%d" % ref0.utm0.utm_zone)
        define_out(out, "NAV_LAT0", "%d /* 1e7deg */" % convert_angle(S.fp_wgs84.posn_lat))
        define_out(out, "NAV_LON0", "%d /* 1e7deg */" % convert_angle(S.fp_wgs84.posn_long))
        define_out(out, "NAV_ALT0", "%.0f /* mm above msl */" % (1000.0 * S.ground_alt))
        define_out(out, "NAV_MSL0", "%.0f /* mm, EGM96 geoid-height (msl) over ellipsoid */" % (1000.0 * geo.egm96_of_wgs84(S.fp_wgs84)))
        define_out(out, "QFU", "%.1f" % qfu)

        waypoints = [DUMMY_WAYPOINT] + waypoints
        hxy = home(waypoints)
        for w in waypoints:
            check_distance(hxy, mdfh, w)
        define_waypoints_indices(out, waypoints)

        define_out(out, "WAYPOINTS_UTM", "{ \\")
        for w in waypoints:
            print_waypoint_utm(out, ref0, alt, w)
        lprintf(out, "};\n")
        define_out(out, "WAYPOINTS_ENU", "{ \\")
        for w in waypoints:
            print_waypoint_enu(out, ref0, alt, w)
        lprintf(out, "};\n")
        define_out(out, "WAYPOINTS_LLA", "{ \\")
        for w in waypoints:
            print_waypoint_lla(out, ref0, alt, w)
        lprintf(out, "};\n")
        define_out(out, "WAYPOINTS_LLA_WGS84", "{ \\")
        for w in waypoints:
            print_waypoint_lla_wgs84(out, ref0, alt, w)
        lprintf(out, "};\n")
        define_out(out, "WAYPOINTS_GLOBAL", "{ \\")
        for w in waypoints:
            print_waypoint_global(out, w)
        lprintf(out, "};\n")
        define_out(out, "NB_WAYPOINT", str(len(waypoints)))

        define_out(out, "FP_BLOCKS", "{ \\")
        for b in blocks:
            out.write(' "%s" , \\\n' % ext_xml.attrib(b, "name"))
        lprintf(out, "}\n")
        define_out(out, "NB_BLOCK", str(len(blocks)))

        define_out(out, "GROUND_ALT", sof(S.ground_alt))
        define_out(out, "GROUND_ALT_CM", "%.0f" % (100.0 * S.ground_alt))
        define_out(out, "SECURITY_HEIGHT", sof(S.security_height))
        define_out(out, "SECURITY_ALT", sof(S.security_height + S.ground_alt))
        define_out(out, "HOME_MODE_HEIGHT", sof(home_mode_height))
        define_out(out, "MAX_DIST_FROM_HOME", sof(mdfh))

        try:
            gma = get_float("geofence_max_alt")
            if gma < S.ground_alt:
                _err("\nError: Geofence max altitude below ground alt (%.0f < %.0f)\n" % (gma, S.ground_alt))
                raise SystemExit(1)
            elif gma < (S.ground_alt + S.security_height):
                _err("\nError: Geofence max altitude below security height (%.0f < (%.0f+%.0f))\n" % (gma, S.ground_alt, S.security_height))
                raise SystemExit(1)
            elif gma < (S.ground_alt + home_mode_height):
                _err("\nError: Geofence max altitude below ground alt + home mode height (%.0f < (%.0f+%.0f))\n" % (gma, S.ground_alt, home_mode_height))
                raise SystemExit(1)
            elif gma < float_of_string(alt):
                _err("\nWarning: Geofence max altitude below default waypoint alt (%.0f < %.0f)\n" % (gma, float_of_string(alt)))
            define_out(out, "GEOFENCE_MAX_ALTITUDE", sof(gma))
            _err("\nNOTICE: Geofence max altitude set to %.0f\n" % gma)
        except Exception:
            pass

        try:
            gmh = get_float("geofence_max_height")
            if gmh < S.security_height:
                _err("\nError: Geofence max height below security height (%.0f < %.0f)\n" % (gmh, S.security_height))
                raise SystemExit(1)
            elif gmh < home_mode_height:
                _err("\nError: Geofence max height below home mode height (%.0f < %.0f)\n" % (gmh, home_mode_height))
                raise SystemExit(1)
            elif (gmh + S.ground_alt) < float_of_string(alt):
                _err("\nWarning: Geofence max AGL below default waypoint AGL (%.0f < %.0f)\n" % (gmh + S.ground_alt, float_of_string(alt)))
            define_out(out, "GEOFENCE_MAX_HEIGHT", sof(gmh))
            _err("\nNOTICE: Geofence max AGL set to %.0f\n" % gmh)
        except Exception:
            pass

        lprintf(out, "\n")
        idx_wp = [(name_of(w), i) for i, w in enumerate(waypoints)]

        lprintf(out, "\n#ifndef FBW\n\n")
        try:
            sectors_element = ext_xml.child(xml, "sectors")
        except NotFound:
            sectors_element = X.Element("", [], [])
        sectors = [x for x in X.children(sectors_element) if X.tag(x).lower() == "sector"]
        for x in sectors:
            if ext_xml.attrib_opt(x, "type") is not None:
                raise Failure('Error: attribute "type" on flight plan tag "sector" is deprecated and must be removed. All sectors are now dynamics.\n')
        sectors = [parse_wpt_sector(idx_wp, waypoints, x) for x in sectors]
        for s in sectors:
            print_inside_sector(out, s)
        try:
            gs = X.attrib(xml, "geofence_sector")
            lprintf(out, "\n#define InGeofenceSector(_x, _y) %s(_x, _y)\n" % inside_function(gs))
        except Exception:
            pass
        lprintf(out, "\n#endif\n")

        lprintf(out, "\n#ifdef NAV_C\n\n")
        for v in variables:
            print_var_impl(out, abi_msgs, v)
        lprintf(out, "\n")
        print_auto_init_bindings(out, abi_msgs, variables)
        lprintf(out, "\n")
        print_enter_exit_functions(out, blocks)
        lprintf(out, "static inline void auto_nav(void) {\n")
        right()
        for e in global_exceptions:
            print_exception(out, e)
        lprintf(out, "switch (nav_block) {\n")
        right()
        print_blocks(out, idx_wp, blocks)
        lprintf(out, "default: break;\n")
        left()
        lprintf(out, "}\n")
        left()
        lprintf(out, "}\n")
        lprintf(out, "#endif // NAV_C\n")
        finish_out(out, h_name)


def dump_flight_plan(xml, out_file):
    with open_out(out_file) as out:
        blocks = X.children(ext_xml.child(xml, "blocks"))
        xml_stages = X.Element("stages", [], indexed_stages(blocks))
        dump_xml = X.Element("dump", [], [xml, xml_stages])
        out.write("%s\n" % ext_xml.to_string_fmt(dump_xml))


def ocaml_dirname(name):
    """Filename.dirname (Unix generic_dirname)."""
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


def generate(flight_plan, xml_file, out_fp, dump=False):
    reinit()
    xml = flight_plan.xml
    S.fp_wgs84 = georef_of_xml(xml)
    xml = check_geo_ref(S.fp_wgs84, xml)
    d = ocaml_dirname(xml_file)
    xml = FP.process_includes(d, xml)
    xml = FP.process_paths(xml)
    xml = FP.process_relative_waypoints(xml)
    blocks = X.children(ext_xml.child(xml, "blocks")) + [HOME_BLOCK]
    xml = ext_xml.subst_child("blocks", index_blocks(element("blocks", [], blocks)), xml)
    waypoints = X.children(ext_xml.child(xml, "waypoints"))
    frame = ext_xml.attrib_or_default(xml, "wp_frame", "UTM")
    fu = frame.upper()
    if fu not in ("UTM", "LTP"):
        raise Failure('Error: unkown wp_frame "' + frame + '". Use "utm" or "ltp"')
    S.ground_alt = float_attrib(xml, "ground_alt")
    # record fields evaluated right to left: geo0, ecef0, utm0
    ecef0 = geo.ecef_of_geo(S.fp_wgs84, S.ground_alt)
    utm0 = geo.utm_of(S.fp_wgs84)
    ref0 = Ref0(fu, utm0, ecef0, S.fp_wgs84)
    if fu == "UTM":
        def rel(wgs84):
            utm = geo.utm_of(wgs84, zone=ref0.utm0.utm_zone)
            return (utm.utm_x - ref0.utm0.utm_x, utm.utm_y - ref0.utm0.utm_y)
        waypoints = [localize_waypoint(rel, w) for w in waypoints]
    xml = ext_xml.subst_child("waypoints", element("waypoints", [], waypoints), xml)
    if dump:
        dump_flight_plan(xml, out_fp)
    else:
        print_flight_plan_h(xml, ref0, xml_file, out_fp)
