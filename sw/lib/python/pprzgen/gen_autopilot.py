"""Port of sw/tools/generators/gen_autopilot.ml: autopilot_core_*.h"""

import os
import re
import sys
import tempfile

from .ocaml import concat as _concat
from . import xml_light as X
from . import ext_xml
from .gen_common import singletonize
from .xml2h import begin_out
from .ocaml import Failure

# like the OCaml global `margin` ref: shared by all calls in the process
_margin = [0]


def right():
    _margin[0] += 2


def left():
    _margin[0] -= 2


def lprintf(out, s):
    out.write(" " * _margin[0] + s)


def _children_tagged(x, t):
    return [m for m in X.children(x) if X.tag(m) == t]


def get_state_machines(ap):
    return _children_tagged(ap, "state_machine")


def get_modes(sm):
    return _children_tagged(sm, "mode")


def get_ap_control_block(ap):
    return _children_tagged(ap, "control_block")


def _child_or_empty(sm, t):
    try:
        return ext_xml.child(sm, t)
    except Exception:
        return X.Element(t, [], [])


def has_modules(sm):
    try:
        return len(X.children(ext_xml.child(sm, "modules"))) > 0
    except Exception:
        return False


def print_includes(includes, out):
    for i in X.children(includes):
        t = X.tag(i)
        if t == "include":
            lprintf(out, '#include "%s"\n' % ext_xml.attrib(i, "name"))
        elif t == "define":
            name = ext_xml.attrib(i, "name")
            value = ext_xml.attrib(i, "value")
            try:
                cond = X.attrib(i, "cond")
            except Exception:
                cond = None
            if cond is None:
                lprintf(out, "#define %s %s\n" % (name, value))
            else:
                lprintf(out, "#%s\n" % cond)
                lprintf(out, "#define %s %s\n" % (name, value))
                lprintf(out, "#endif\n")
        else:
            raise Failure("[gen_autopilot] Unknown includes type")


def print_mode_name(sm_name, name, modes=None):
    if modes is None:
        return sm_name.upper() + "_MODE_" + name.upper()
    if any(X.attrib(m, "name") == name for m in modes):
        return sm_name.upper() + "_MODE_" + name.upper()
    return name


def print_modes(modes, sm_name, out):
    idx = 0
    for m in modes:
        try:
            name = X.attrib(m, "name")
            lprintf(out, "#define %s %d\n" % (print_mode_name(sm_name, name), idx))
            idx += 1
        except Exception:
            pass


def find_default_mode(modes):
    def is_default(m):
        for s in X.children(m):
            if X.tag(s) == "select" and X.attrib(s, "cond") == "$DEFAULT_MODE":
                return True
        return False
    default = [m for m in modes if is_default(m)]
    if len(default) == 0:
        if not modes:
            raise Failure("hd")
        mode = modes[0]
    elif len(default) == 1:
        mode = default[0]
    else:
        raise Failure("Autopilot Core Error: only one default mode can be set")
    return X.attrib(mode, "name")


def print_ap_init(modes, name, out):
    lprintf(out, "\nstatic inline void autopilot_core_%s_init(void) {\n" % name)
    right()
    lprintf(out, "autopilot_mode_%s = MODE_STARTUP;\n" % name)
    lprintf(out, "private_autopilot_mode_%s = autopilot_mode_%s;\n" % (name, name))
    lprintf(out, "last_autopilot_mode_%s = autopilot_mode_%s;\n" % (name, name))
    lprintf(out, "autopilot_core_%s_set_mode(autopilot_mode_%s, TRUE);\n" % (name, name))
    left()
    lprintf(out, "}\n")


def print_test_select(modes, mode_select, name, out):
    lprintf(out, "\nstatic inline uint8_t autopilot_core_%s_mode_select(void) {\n" % name)
    right()

    def print_select(mode_name, s):
        cond = X.attrib(s, "cond")
        try:
            parts = [x for x in X.attrib(s, "exception").split("|") if x != ""]
            except_ = " || ".join("private_autopilot_mode_%s != %s" % (name, print_mode_name(name, e)) for e in parts)
        except Exception:
            except_ = ""
        if cond == "$DEFAULT_MODE":
            return
        if len(except_) == 0:
            lprintf(out, "if (%s) { return %s; }\n" % (cond, print_mode_name(name, mode_name, modes)))
        else:
            lprintf(out, "if ((%s) && (%s)) { return %s; }\n" % (cond, except_, print_mode_name(name, mode_name, modes)))

    for m in modes:
        select = [s for s in X.children(m) if X.tag(s) == "select"]
        for s in select:
            print_select(X.attrib(m, "name"), s)
    for s in X.children(mode_select):
        print_select(X.attrib(s, "mode"), s)
    lprintf(out, "return private_autopilot_mode_%s;\n" % name)
    left()
    lprintf(out, "}\n")


def print_test_exception(modes, name, out):
    def print_exception(ex):
        deroute = X.attrib(ex, "deroute")
        cond = X.attrib(ex, "cond")
        if deroute == "$LAST_MODE":
            lprintf(out, "if (%s) { return last_autopilot_mode_%s; }\n" % (cond, name))
        else:
            lprintf(out, "if (%s) { return %s; }\n" % (cond, print_mode_name(name, deroute)))

    lprintf(out, "\nstatic inline uint8_t autopilot_core_%s_mode_exceptions(uint8_t mode) {\n" % name)
    right()
    lprintf(out, "switch ( mode ) {\n")
    right()
    for m in modes:
        lprintf(out, "case %s :\n" % print_mode_name(name, X.attrib(m, "name")))
        right()
        lprintf(out, "{\n")
        right()
        for e in _children_tagged(m, "exception"):
            print_exception(e)
        left()
        lprintf(out, "}\n")
        lprintf(out, "break;\n")
        left()
    lprintf(out, "default:\n")
    right()
    lprintf(out, "break;\n")
    left()
    left()
    lprintf(out, "}\n")
    lprintf(out, "return mode;\n")
    left()
    lprintf(out, "}\n")


def print_global_exceptions(exceptions, name, out):
    lprintf(out, "\nstatic inline uint8_t autopilot_core_%s_global_exceptions(uint8_t mode) {\n" % name)
    right()
    for ex in X.children(exceptions):
        lprintf(out, "if (%s) { mode = %s; }\n" % (X.attrib(ex, "cond"), print_mode_name(name, X.attrib(ex, "deroute"))))
    lprintf(out, "return mode;\n")
    left()
    lprintf(out, "}\n")


def print_set_mode(modes, name, out):
    def print_case(mode, f):
        lprintf(out, "case %s :\n" % print_mode_name(name, X.attrib(mode, "name")))
        right()
        for x in X.children(f):
            lprintf(out, "%s;\n" % ext_xml.attrib(x, "fun"))
        lprintf(out, "break;\n")
        left()

    def print_switch(var, modes, t):
        lprintf(out, "// switch over %s functions\n" % t)
        lprintf(out, "switch ( %s ) {\n" % var)
        right()
        for m in modes:
            try:
                print_case(m, ext_xml.child(m, t))
            except Exception:
                pass
        lprintf(out, "default:\n")
        right()
        lprintf(out, "break;\n")
        left()
        left()
        lprintf(out, "}\n")

    lprintf(out, "\nstatic inline void autopilot_core_%s_set_mode(uint8_t new_mode, bool force) {\n\n" % name)
    right()
    lprintf(out, "if (new_mode == private_autopilot_mode_%s && !force) return;\n\n" % name)
    print_switch("private_autopilot_mode_" + name, modes, "on_exit")
    out.write("\n")
    print_switch("new_mode", modes, "on_enter")
    out.write("\n")
    lprintf(out, "last_autopilot_mode_%s = private_autopilot_mode_%s;\n" % (name, name))
    lprintf(out, "private_autopilot_mode_%s = new_mode;\n" % name)
    lprintf(out, "autopilot_mode_%s = new_mode;\n" % name)
    left()
    lprintf(out, "}\n")


def print_ap_periodic(modes, ctrl_block, main_freq, name, out):
    def print_call(call):
        try:
            s = X.attrib(call, "store")
            words = [w for w in s.split(" ") if w != ""]
            if not words:
                raise Failure("hd")
            store = "%s = " % words[-1]
        except Exception:
            store = ""
        try:
            f = X.attrib(call, "fun")
            try:
                cond = "if (" + X.attrib(call, "cond") + ") { " + store + f + "; }\n"
            except Exception:
                cond = store + f + ";\n"
            lprintf(out, cond)
        except Exception:
            pass

    def get_control_block(c):
        for n in ctrl_block:
            if X.attrib(c, "name") == X.attrib(n, "name"):
                return n
        raise Failure("Could not find block %s" % X.attrib(c, "name"))

    def print_ctrl(ctrl):
        for c in X.children(ctrl):
            t = X.tag(c)
            if t == "call":
                print_call(c)
            elif t == "call_block":
                for cc in X.children(get_control_block(c)):
                    print_call(cc)

    def print_prescaler(freq, ctrl):
        lprintf(out, "{\n")
        right()
        lprintf(out, "static uint16_t prescaler = 0;\n")
        lprintf(out, "prescaler++;\n")
        lprintf(out, "if (prescaler >= (uint16_t)(%s / %s)) {\n" % (main_freq, freq))
        right()
        lprintf(out, "prescaler = 0;\n")
        print_ctrl(ctrl)
        left()
        lprintf(out, "}\n")
        left()
        lprintf(out, "}\n")

    def get_stores(acc, xs):
        for h in xs:
            try:
                s = X.attrib(h, "store")
                found = True
            except Exception:
                found = False
            if found:
                acc = [s] + acc
            else:
                acc = get_stores(acc, X.children(h))
        return acc

    stores = singletonize(get_stores([], modes + ctrl_block))
    lprintf(out, "\nstatic inline void autopilot_core_%s_periodic_task(void) {\n\n" % name)
    right()
    for s in stores:
        lprintf(out, "%s;\n" % s)
    lprintf(out, "uint8_t mode = autopilot_core_%s_mode_select();\n" % name)
    lprintf(out, "mode = autopilot_core_%s_mode_exceptions(mode);\n" % name)
    lprintf(out, "mode = autopilot_core_%s_global_exceptions(mode);\n" % name)
    lprintf(out, "autopilot_core_%s_set_mode(mode, FALSE);\n\n" % name)
    lprintf(out, "switch ( private_autopilot_mode_%s ) {\n" % name)
    right()
    for m in modes:
        lprintf(out, "case %s :\n" % print_mode_name(name, X.attrib(m, "name")))
        right()
        lprintf(out, "{\n")
        right()
        for c in _children_tagged(m, "control"):
            try:
                freq = X.attrib(c, "freq")
            except Exception:
                freq = None
            if freq is None:
                print_ctrl(c)
            else:
                print_prescaler(freq, c)
        left()
        lprintf(out, "}\n")
        lprintf(out, "break;\n\n")
        left()
    lprintf(out, "default:\n")
    right()
    lprintf(out, "break;\n")
    left()
    left()
    lprintf(out, "}\n")
    left()
    lprintf(out, "}\n")


def parse_and_gen_modes(xml_file, ap_name, main_freq, h_dir, sm):
    fd, tmp_file = tempfile.mkstemp(prefix="pprz_ap_", suffix=".h")
    os.chmod(tmp_file, 0o644)
    out = os.fdopen(fd, "w", encoding="latin-1", newline="")
    try:
        name = X.attrib(sm, "name")
        name_up = name.upper()
        begin_out(out, xml_file, "AUTOPILOT_CORE_" + name_up + "_H")
        out.write("/*** %s ***/\n\n" % ap_name)
        out.write("#ifdef AUTOPILOT_CORE_%s_C\n" % name_up)
        out.write("#define EXTERN_%s\n" % name_up)
        out.write("#else\n")
        out.write("#define EXTERN_%s extern\n" % name_up)
        out.write("#endif\n")
        modes = get_modes(sm)
        ctrl_block = get_ap_control_block(sm)
        print_modes(modes, name_up, out)
        out.write("\nEXTERN_%s uint8_t autopilot_mode_%s;\n" % (name_up, name))
        out.write("\n#ifdef AUTOPILOT_CORE_%s_C\n\n" % name_up)
        print_includes(_child_or_empty(sm, "includes"), out)
        if has_modules(sm):
            out.write('\n#include "generated/modules.h"\n')
        out.write("\nuint8_t private_autopilot_mode_%s;\n" % name)
        out.write("uint8_t last_autopilot_mode_%s;\n\n" % name)
        default_mode = find_default_mode(modes)
        out.write("#ifndef MODE_STARTUP\n")
        out.write("#define MODE_STARTUP %s\n" % print_mode_name(name_up, default_mode))
        out.write("#endif\n")
        print_test_select(modes, _child_or_empty(sm, "mode_selection"), name, out)
        print_test_exception(modes, name, out)
        print_global_exceptions(_child_or_empty(sm, "exceptions"), name, out)
        print_set_mode(modes, name, out)
        sm_freq = X.attrib(sm, "freq")  # evaluated even when main_freq is given
        freq = sm_freq if main_freq is None else main_freq
        print_ap_init(modes, name, out)
        print_ap_periodic(modes, ctrl_block, freq, name, out)
        out.write("\n#endif // AUTOPILOT_CORE_%s_C\n" % name_up)
        out.write("\n#endif // AUTOPILOT_CORE_%s_H\n" % name_up)
        out.close()
        h_file = _concat(h_dir, "autopilot_core_%s.h" % name)
        if os.path.exists(h_file):
            os.remove(h_file)
        import shutil
        shutil.move(tmp_file, h_file)
    except Exception:
        if not out.closed:
            out.close()
        if os.path.exists(tmp_file):
            os.remove(tmp_file)


def generate(autopilot, ap_freq, out_dir):
    try:
        ap_name = ext_xml.attrib_or_default(autopilot.xml, "name", "Autopilot")
        for sm in get_state_machines(autopilot.xml):
            parse_and_gen_modes(autopilot.filename, ap_name, ap_freq, out_dir, sm)
    except Exception:
        sys.stderr.write("gen_autopilot: What the heck? Something went wrong...\n")
        sys.stderr.flush()
        raise SystemExit(1)
