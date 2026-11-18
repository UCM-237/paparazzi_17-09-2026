"""Port of sw/tools/generators/gen_airframe.ml: generates airframe.h"""

import re
import sys

from . import xml_light as X
from . import ext_xml, pprzlink
from .xml2h import define_out, define_string_out, begin_out, finish_out, xml_error, sprint_float_array, open_out
from .ocaml import (Failure, NotFound, Hashtbl, float_of_string, int_of_string, string_of_float,
                    fdiv, truncate, fmod, MIN_INT)

MAX_PPRZ = 9600.0

fos = float_of_string


def sof(x):
    if fmod(x, 1.0) == 0.0:
        return "%.0f" % x
    return string_of_float(x)


def _exit(code):
    sys.stdout.flush()
    sys.stderr.flush()
    raise SystemExit(code)


def _error_and_exit(e):
    sys.stderr.write(e + "\n")
    sys.stderr.flush()
    _exit(1)


class _State:
    def __init__(self):
        self.servos_drivers = Hashtbl(3)


def define_integer(out, name, v, n):
    max_val = 1 << n
    if v >= 0.0:
        s = 1
    else:
        v, s = -v, -1

    def pr(num, den):
        define_out(out, name + "_NUM", str(num))
        define_out(out, name + "_DEN", str(den))

    a, x = truncate(v), v
    num, den = (1, truncate(v)), (0, 1)
    while True:
        x1 = fdiv(1.0, x - float(a))
        a1 = truncate(x1)
        num1, num2 = num
        num3 = a1 * num2 + num1
        den1, den2 = den
        den3 = a1 * den2 + den1
        if num3 > max_val or den3 > max_val:
            pr(num2, s * den2)
            return
        if fdiv(float(num3), float(den3)) == v:
            pr(num3, s * den3)
            return
        a, x, num, den = a1, x1, (num2, num3), (den2, den3)


class _ConvFail(Exception):
    pass


def convert_value_with_code_unit_coef_of_xml(xml):
    try:
        u = X.attrib(xml, "unit")
    except Exception:
        raise Failure("Unit conversion error")
    cu = ext_xml.attrib_or_default(xml, "code_unit", "")
    if u == cu:
        raise Failure("Not converting")
    try:
        conv = pprzlink.scale_of_units(u, cu)
    except pprzlink.UnitConversionError as e:
        sys.stderr.write(f"Unit conversion error: {e}\n")
        sys.stderr.flush()
        _exit(1)
    except pprzlink.UnknownConversion as e:
        sys.stderr.write(f"Warning: unknown unit conversion: from {e.su} to {e.scu}\n")
        sys.stderr.flush()
        raise Failure("Unknown unit conversion")
    except Exception:
        raise Failure("Unit conversion error")
    try:
        v = ext_xml.float_attrib(xml, "value")
    except Exception:
        sys.stderr.write("Error: Unit conversion of parameter %s impossible because '%s' is not a float\n"
                         % (X.attrib(xml, "name"), X.attrib(xml, "value")))
        sys.stderr.flush()
        _exit(1)
    return v * conv


def get_value_converted(x):
    try:
        return string_of_float(convert_value_with_code_unit_coef_of_xml(x))
    except Exception:
        return ext_xml.display_entities(ext_xml.attrib(x, "value"))


def _str_split(regex, s):
    parts = re.split(regex, s)
    while parts and parts[0] == "":
        parts.pop(0)
    while parts and parts[-1] == "":
        parts.pop()
    return parts


def _rm_spaces(s):
    s = re.sub(r"^ *", "", s, flags=re.M)
    return re.sub(r" *$", "", s, flags=re.M)


def string_from_type(name, t, x):
    try:
        v = X.attrib(x, "value")
    except Exception:
        v = None
    c = None if len(X.children(x)) == 0 else X.children(x)
    if v is None and c is None:
        _error_and_exit(f"Define {name} requires 'value' attribute or 'field' node")
    if v is not None and c is not None:
        _error_and_exit(f"Define {name} can't support both 'value' attribute or 'field' node")
    if v is not None:
        val = ("value", get_value_converted(x))
    else:
        val = ("fields", c)

    def value_from_type(t, val):
        def sprint_array(t, val):
            if val[0] == "value":
                vs = _str_split(r"[,;]", val[1])
                return "{ " + " , ".join(value_from_type(t, ("value", vl)) for vl in vs) + " }"
            return "{ " + " , ".join(value_from_type(t, ("fields", X.children(f))) for f in val[1]) + " }"

        def sprint_matrix(l):
            return "{ " + " , ".join(string_from_type(name, ext_xml.attrib_or_default(f, "type", ""), f) for f in l) + " }"

        def sprint_struct(l):
            fl = []
            for f in l:
                try:
                    n = ext_xml.attrib(f, "name")
                    fl.append(".%s = %s" % (n, string_from_type(name, ext_xml.attrib_or_default(f, "type", ""), f)))
                except Exception:
                    fl.append(string_from_type(name, ext_xml.attrib_or_default(f, "type", ""), f))
            return "{ " + " , ".join(fl) + " }"

        if val[0] == "fields":
            if t in ("matrix", "array"):
                return sprint_matrix(X.children(x))
            if t == "struct":
                return sprint_struct(X.children(x))
            _error_and_exit(f"Unknown define type for fields of {name} (specify 'matrix', 'array' or 'struct')")
        vv = val[1]
        if t == "float":
            try:
                return string_of_float(float_of_string(_rm_spaces(vv)))
            except Exception:
                _error_and_exit(f"Define value {name} = {vv} is not compatible with type float")
        if t == "int":
            try:
                return str(int_of_string(_rm_spaces(vv)))
            except Exception:
                _error_and_exit(f"Define value {name} = {vv} is not compatible with type int")
        if t == "string":
            return '"' + _rm_spaces(vv) + '"'
        if t == "array":
            return sprint_array("", val)
        if t == "float[]":
            return sprint_array("float", val)
        if t == "int[]":
            return sprint_array("int", val)
        if t == "string[]":
            return sprint_array("string", val)
        if t == "matrix":
            return sprint_matrix(X.children(x))
        if t == "struct":
            return sprint_struct(X.children(x))
        return vv

    return value_from_type(t, val)


def parse_element(out, prefix, s):
    if X.tag(s) != "define":
        xml_error("define")
    try:
        name = prefix + ext_xml.attrib(s, "name")
        t = ext_xml.attrib_or_default(s, "type", "")
        define_out(out, name, string_from_type(name, t, s))
        define_integer(out, name, ext_xml.float_attrib(s, "value"), ext_xml.int_attrib(s, "integer"))
    except Exception:
        pass


def print_reverse_servo_table(out, driver, servos):
    d = "" if driver == "Default" else "_" + driver.upper()
    for what in ("min", "max", "idx"):
        out.write(f"static inline int get_servo_{what}{d}(int _idx) {{\n")
        out.write("  switch (_idx) {\n")
        for c in servos:
            n = ext_xml.attrib(c, "name")
            out.write(f"    case SERVO_{n}_DRIVER_NO: return SERVO_{n}_{what.upper()};\n")
        out.write("    default: return 0;\n")
        out.write("  };\n")
        out.write("}\n\n")


def parse_servo(st, out, driver, c):
    shortname = ext_xml.attrib(c, "name")
    name = "SERVO_" + shortname
    no_servo = int_of_string(ext_xml.attrib(c, "no"))
    global_idx = st.servos_drivers.length()
    define_out(out, name + "_DRIVER_NO", str(no_servo))
    define_out(out, name + "_IDX", str(global_idx))
    s_min = fos(ext_xml.attrib(c, "min"))
    neutral = fos(ext_xml.attrib(c, "neutral"))
    s_max = fos(ext_xml.attrib(c, "max"))
    travel_up = (s_max - neutral) / MAX_PPRZ
    travel_down = (neutral - s_min) / MAX_PPRZ
    define_out(out, name + "_NEUTRAL", sof(neutral))
    define_out(out, name + "_TRAVEL_UP", sof(travel_up))
    define_integer(out, name + "_TRAVEL_UP", travel_up, 16)
    define_out(out, name + "_TRAVEL_DOWN", sof(travel_down))
    define_integer(out, name + "_TRAVEL_DOWN", travel_down, 16)
    s_min, s_max = _ocaml_min(s_min, s_max), _ocaml_max(s_min, s_max)
    define_out(out, name + "_MAX", sof(s_max))
    define_out(out, name + "_MIN", sof(s_min))
    out.write("\n")
    st.servos_drivers.add(shortname, (driver, global_idx))


def _ocaml_min(a, b):
    # Stdlib.min: "if a <= b then a else b" (comparisons with nan are false)
    return a if a <= b else b


def _ocaml_max(a, b):
    return a if a >= b else b


_PPRZ_VALUE = re.compile(r"@([A-Z_0-9]+)")
_VAR_VALUE = re.compile(r"\$([_a-z0-9]+)")


def preprocess_value(s, v, prefix):
    s = _PPRZ_VALUE.sub(lambda m: f"{v}[{prefix}_{m.group(1)}]", s)
    return _VAR_VALUE.sub(lambda m: "_var_" + m.group(1), s)


def get_list_of_drivers(st):
    l = []
    for _s, (d, _) in st.servos_drivers.items():
        if d not in l:
            l.insert(0, d)
    return l


def print_actuators_idx(st, out):
    for s, (d, _i) in st.servos_drivers.items():
        out.write(f"#define Set_{s}_Servo(actuator_value_pprz) {{ \\\n")
        out.write("  int32_t servo_value;\\\n")
        out.write("  int32_t command_value;\\\n\\\n")
        out.write(f"  actuators[SERVO_{s}_IDX].pprz_val = ClipAbs( actuator_value_pprz, MAX_PPRZ); \\\n")
        out.write(f"  command_value = actuator_value_pprz * (actuator_value_pprz>0 ? SERVO_{s}_TRAVEL_UP_NUM : SERVO_{s}_TRAVEL_DOWN_NUM); \\\n")
        out.write(f"  command_value /= actuator_value_pprz>0 ? SERVO_{s}_TRAVEL_UP_DEN : SERVO_{s}_TRAVEL_DOWN_DEN; \\\n")
        out.write(f"  servo_value = SERVO_{s}_NEUTRAL + command_value; \\\n")
        out.write(f"  actuators[SERVO_{s}_IDX].driver_val = Clip(servo_value, SERVO_{s}_MIN, SERVO_{s}_MAX); \\\n")
        out.write(f"  Actuator{d}Set(SERVO_{s}_DRIVER_NO, actuators[SERVO_{s}_IDX].driver_val); \\\n")
        out.write("}\n\n")
    define_out(out, "ACTUATORS_NB", str(st.servos_drivers.length()))
    out.write("\n")


def parse_command_laws(out, command):
    a = lambda s: ext_xml.attrib(command, s)
    t = X.tag(command)
    if t == "set":
        servo = a("servo")
        value = a("value")
        v = preprocess_value(value, "values", "COMMAND")
        out.write(f"  actuator_value_pprz = {v}; \\\n")
        out.write(f"  Set_{servo}_Servo(actuator_value_pprz); \\\n\\\n")
    elif t == "let":
        var = a("var")
        value = a("value")
        v = preprocess_value(value, "values", "COMMAND")
        out.write(f"  int32_t _var_{var} = {v}; \\\n")
    elif t == "call":
        out.write(f"  {a('fun')}; \\\n\\\n")
    elif t == "ratelimit":
        var = a("var")
        value = a("value")
        rate_min = a("rate_min")
        rate_max = a("rate_max")
        v = preprocess_value(value, "values", "COMMAND")
        out.write(f"  static int32_t _var_{var} = 0; _var_{var} += Clip(({v}) - (_var_{var}), ({rate_min}), ({rate_max})); \\\n")
    elif t == "define":
        parse_element(out, "", command)
    else:
        xml_error("set|let")


def parse_rc_commands(out, rc):
    a = lambda s: ext_xml.attrib(rc, s)
    t = X.tag(rc)
    if t == "set":
        com = a("command")
        value = a("value")
        v = preprocess_value(value, "_rc_array", "RADIO")
        out.write(f"  _commands_array[COMMAND_{com}] = {v};\\\n")
    elif t == "let":
        var = a("var")
        value = a("value")
        v = preprocess_value(value, "rc_values", "RADIO")
        out.write(f"  int16_t _var_{var} = {v};\\\n")
    elif t == "define":
        parse_element(out, "", rc)
    else:
        xml_error("set|let")


def parse_ap_only_commands(out, ap_only):
    if X.tag(ap_only) == "copy":
        com = ext_xml.attrib(ap_only, "command")
        out.write(f"  commands[COMMAND_{com}] = ap_commands[COMMAND_{com}];\\\n")
    else:
        xml_error("copy")


def parse_command(out, command, no):
    command_name = "COMMAND_" + ext_xml.attrib(command, "name")
    failsafe_value = int_of_string(ext_xml.attrib(command, "failsafe_value"))
    group = ext_xml.attrib_or_default(command, "group", "REAL")
    define_out(out, command_name, str(no))
    return (failsafe_value, group)


def count_commands_by_type(commands_params):
    acc = []
    for _fs, subtype in commands_params:
        count = 0
        for k, c in acc:
            if k == subtype:
                count = c
                break
        rest = list(acc)
        for j, (k, _) in enumerate(rest):
            if k == subtype:
                del rest[j]
                break
        acc = [(subtype, count + 1)] + rest
    return acc


def parse_heli_curves(out, hc):
    a = lambda s: ext_xml.attrib(hc, s)
    if X.tag(hc) != "curve":
        xml_error("mixer")
    throttle = a("throttle")
    rpm = ext_xml.attrib_or_default(hc, "rpm", "")
    collective = a("collective")
    nb_throttle = len(_str_split(",", throttle))
    nb_rpm = len(_str_split(",", rpm))
    nb_collective = len(_str_split(",", collective))
    if nb_throttle < 1:
        raise Failure(f"Need at least one value in throttle curve for a throttle ('{throttle}', '{collective}')")
    if nb_throttle != nb_collective:
        raise Failure(f"Amount of throttle points not the same as collective in throttle curve ('{throttle}', '{collective}')")
    if nb_throttle != nb_rpm and nb_rpm != 0:
        raise Failure(f"Amount of throttle points not the same as rpm in throttle curve ('{throttle}', '{collective}', '{rpm}')")
    out.write(f"  {{.nb_points = {nb_throttle}, \\\n")
    out.write(f"   .throttle = {{{throttle}}}, \\\n")
    if nb_rpm != 0:
        out.write(f"   .rpm = {{{rpm}}}, \\\n")
    else:
        out.write("   .rpm = {0xFFFF}, \\\n")
    out.write(f"   .collective = {{{collective}}}}}, \\\n")


def parse_section(st, out, ac_id, s):
    t = X.tag(s)
    if t == "section":
        prefix = ext_xml.attrib_or_default(s, "prefix", "")
        define_out(out, "SECTION_" + ext_xml.attrib(s, "name"), "1")
        for e in X.children(s):
            parse_element(out, prefix, e)
        out.write("\n")
    elif t == "servos":
        driver = ext_xml.attrib_or_default(s, "driver", "Default")
        servos = X.children(s)
        m = MIN_INT
        for sv in reversed(servos):  # List.fold_right
            m = max(int_of_string(ext_xml.attrib(sv, "no")), m)
        nb_servos = m + 1
        define_out(out, "SERVOS_%s_NB" % driver.upper(), str(nb_servos))
        out.write('#include "modules/actuators/actuators_%s.h"\n' % driver.lower())
        out.write("\n")
        for sv in servos:
            parse_servo(st, out, driver, sv)
        print_reverse_servo_table(out, driver, servos)
        out.write("\n")
    elif t == "commands":
        commands = X.children(s)
        params = [parse_command(out, c, i) for i, c in enumerate(commands)]
        for subtype, count in count_commands_by_type(params):
            define_out(out, "COMMANDS_NB_%s" % subtype, str(count))
        define_out(out, "COMMANDS_NB", str(len(commands)))
        names = ", ".join('"' + ext_xml.attrib(ax, "name") + '"' for ax in commands)
        out.write(f"#define COMMAND_NAMES {{ {names} }}\n\n")
        define_out(out, "COMMANDS_FAILSAFE", sprint_float_array([str(fs) for fs, _ in params]))
        out.write("\n\n")
    elif t == "rc_commands":
        out.write("#define SetCommandsFromRC(_commands_array, _rc_array) { \\\n")
        for e in X.children(s):
            parse_rc_commands(out, e)
        out.write("}\n\n")
    elif t == "auto_rc_commands":
        out.write("#define SetAutoCommandsFromRC(_commands_array, _rc_array) { \\\n")
        for e in X.children(s):
            parse_rc_commands(out, e)
        out.write("}\n\n")
    elif t == "ap_only_commands":
        out.write("#define SetApOnlyCommands(ap_commands) { \\\n")
        for e in X.children(s):
            parse_ap_only_commands(out, e)
        out.write("}\n\n")
    elif t == "command_laws":
        print_actuators_idx(st, out)
        drivers = get_list_of_drivers(st)
        out.write("#define AllActuatorsInit() { \\\n")
        for d in drivers:
            out.write(f"  Actuators{d}Init();\\\n")
        out.write("}\n\n")
        out.write("#define AllActuatorsCommit() { \\\n")
        for d in drivers:
            out.write(f"  Actuators{d}Commit();\\\n")
        out.write("}\n\n")
        out.write("#define SetActuatorsFromCommands(values, AP_MODE) { \\\n")
        out.write("  int32_t actuator_value_pprz;\\\n\\\n")
        for e in X.children(s):
            parse_command_laws(out, e)
        out.write("  AllActuatorsCommit(); \\\n")
        out.write("}\n\n")
    elif t == "heli_curves":
        default = ext_xml.attrib_or_default(s, "default", "0")
        curves = X.children(s)
        nb_points = 0
        for c in reversed(curves):
            nb_points = max(len(_str_split(",", ext_xml.attrib(c, "throttle"))), nb_points)
        define_out(out, "THROTTLE_CURVE_MODE_INIT", default)
        define_out(out, "THROTTLE_CURVES_NB", str(len(curves)))
        define_out(out, "THROTTLE_POINTS_NB", str(nb_points))
        out.write("#define THROTTLE_CURVES { \\\n")
        for c in curves:
            parse_heli_curves(out, c)
        out.write("}\n\n")
    elif t == "include":
        filename = ext_xml.attrib(s, "href").replace("$AC_ID", ac_id)
        subxml = ext_xml.parse_file(filename)
        out.write(f"/* XML {filename} */")
        out.write("\n")
        for e in X.children(subxml):
            parse_section(st, out, ac_id, e)


H_NAME = "AIRFRAME_H"


def hex_to_bin(s):
    assert len(s) % 2 == 0
    n = len(s)
    b = ["x"] * (2 * n)
    for i in range(n // 2):
        b[4 * i] = "\\"
        x = int(s[2 * i:2 * i + 2], 16)
        o = "%03o" % x
        b[4 * i + 1:4 * i + 4] = list(o)
    return "".join(b)


def generate(airframe, ac_id, ac_name, md5sum, xml_file, out_file):
    st = _State()
    with open_out(out_file) as out:
        begin_out(out, xml_file, H_NAME)
        define_string_out(out, "AIRFRAME_NAME", ac_name)
        define_out(out, "AC_ID", ac_id)
        define_out(out, "MD5SUM", '((uint8_t*)"%s")' % hex_to_bin(md5sum))
        out.write("\n")
        for s in X.children(airframe.xml):
            parse_section(st, out, ac_id, s)
        finish_out(out, H_NAME)
