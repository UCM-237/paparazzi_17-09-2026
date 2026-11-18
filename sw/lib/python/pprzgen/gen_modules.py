"""Port of sw/tools/generators/gen_modules.ml: generates modules.h"""

import re
import sys

from . import expr as E
from .gen_common import singletonize
from .xml2h import define_out, begin_out, finish_out, open_out
from .ocaml import Hashtbl, NotFound, Failure, option_key

TASKS_ORDER = ["mcu", "core", "sensors", "estimation", "radio_control", "control", "actuators", "datalink", "default"]


class _P:
    def __init__(self, out):
        self.out = out
        self.margin = 0

    def right(self):
        self.margin += 2

    def left(self):
        self.margin -= 2

    def lprintf(self, s):
        self.out.write(" " * self.margin + s)

    def with_cond(self, s, cond):
        if cond is None:
            self.lprintf("%s;\n" % s)
        else:
            e = E.parse_expression(cond, prefix="Fp_proc: ")
            self.out.write("#if %s\n%s%s;\n#endif\n" % (E.sprint(e), " " * self.margin, s))


def replace_point(s):
    return s.replace(".", "_")


def print_modules_ids(p, modules):
    for i, m in enumerate(modules):
        p.lprintf("#define MODULE_%s_ID %d\n" % (replace_point(m.name).upper(), i + 1))
    p.lprintf("#define MODULES_NAMES = { \\\n")
    for m in modules:
        p.lprintf(' "%s", \\\n' % m.name)
    p.lprintf("}\n")
    p.lprintf("#define NB_MODULES %d\n" % len(modules))
    max_length = 0
    for m in modules:
        if len(m.name.encode("latin-1")) > max_length:
            max_length = len(m.name.encode("latin-1"))
    p.lprintf("#define MODULES_NAMES_MAX_LEN %d\n" % (max_length + 1))


def print_headers(p, modules):
    p.lprintf('#include "std.h"\n')
    for m in modules:
        dirname = m.name if m.dir is None else m.dir
        for h in m.headers:
            d = dirname if h.directory is None else h.directory
            inc = '#include "%s/%s"' % (d, h.filename)
            if h.filecond is None:
                p.out.write(inc + "\n")
            else:
                p.out.write("#if %s\n%s\n#endif\n" % (h.filecond, inc))


def get_status_name(f, n):
    i = f.find("(")
    return n + "_" + (f[:i] if i >= 0 else f) + "_status"


def get_period_and_freq(pf):
    k = pf[0]
    if k == "unset":
        return ("(1.f / MODULES_FREQUENCY)", "(MODULES_FREQUENCY)")
    if k == "freq":
        return ("(1.f / (" + pf[1] + "))", "(" + pf[1] + ")")
    if k == "period":
        return ("(" + pf[1] + ")", "(1.f / (" + pf[1] + "))")
    return ("(%ff)" % pf[1], "(%ff)" % pf[2])


def get_functions_modulos(modules):
    found = Hashtbl(10)
    idx = 0
    delay = 0.0
    res = []
    for m in modules:
        for x in m.periodics:
            per, _ = get_period_and_freq(x.period_freq)
            if x.delay is not None:
                d = x.delay
                if d > 0.99:
                    sys.stderr.write("Warning: 'delay' attribute should be a float value between 0. and 1.\n")
                    d = d / 65536.0
            else:
                delay = delay + 0.1
                if delay > 0.9:
                    delay = 0.0
                d = delay
            try:
                res.append(((x, m.name, d), (per, found.find(per))))
            except NotFound:
                idx += 1
                found.add(per, idx)
                res.append(((x, m.name, d), (per, idx)))
    return res


def print_function_freq(p, modules):
    p.out.write("\n")
    for m in modules:
        for i in m.periodics:
            fname = i.fname.upper()
            per, f = get_period_and_freq(i.period_freq)
            p.lprintf("#define %s_PERIOD %s\n" % (fname, per))
            p.lprintf("#define %s_FREQ %s\n" % (fname, f))


def print_function_prescalers(p, functions_modulo):
    p.out.write("\n")
    found = set()
    for _, (per, m) in functions_modulo:
        if m not in found:
            p.lprintf("#define PRESCALER_%d (uint32_t)(MODULES_FREQUENCY * %s)\n" % (m, per))
            found.add(m)


def print_status(p, modules):
    p.out.write("\n")
    for m in modules:
        for per in m.periodics:
            if per.autorun != "lock":
                p.lprintf("EXTERN_MODULES uint8_t %s;\n" % get_status_name(per.fname, m.name))


def modules_of_task(modules):
    h = Hashtbl(1)
    for m in modules:
        task = "default" if m.task is None else m.task
        if h.mem(task):
            h.replace(task, h.find(task) + [m])
        else:
            h.add(task, [m])
    mot = []
    for task_name in TASKS_ORDER:
        try:
            tl = h.find(task_name)
        except NotFound:
            tl = []
        h.remove(task_name)
        mot.append((task_name, tl))
    for key, tasks in list(h.items()):
        sys.stderr.write("Warning: adding an unknown task '%s'\n" % key)
        mot = mot + [(key, tasks)]
    return mot


def print_init(p, task_modules):
    task, modules = task_modules
    p.lprintf("\nstatic inline void modules_%s_init(void) {\n" % task)
    p.right()
    for m in modules:
        for init in m.inits:
            p.with_cond(init.iname, init.cond)
        for per in m.periodics:
            if per.autorun == "true":
                p.lprintf("%s = MODULES_START;\n" % get_status_name(per.fname, m.name))
            elif per.autorun == "false":
                p.lprintf("%s = MODULES_STOP;\n" % get_status_name(per.fname, m.name))
    p.left()
    p.lprintf("}\n")


def print_init_functions(p, modules):
    mot = modules_of_task(modules)
    for tm in mot:
        print_init(p, tm)
    p.lprintf("\nstatic inline void modules_init(void) {\n")
    p.right()
    for t, _ in mot:
        p.lprintf("modules_%s_init();\n" % t)
    p.left()
    p.lprintf("}\n")


def print_periodic(p, functions_modulo, task_modules):
    task, modules = task_modules
    names = [m.name for m in modules]
    fm = [x for x in functions_modulo if x[0][1] in names]
    p.lprintf("\nstatic inline void modules_%s_periodic_task(void) {\n" % task)
    p.right()
    for modulo in singletonize([m for _, (_, m) in fm]):
        v = "i%d" % modulo
        p.lprintf("static uint32_t %s; %s++; if (%s>=PRESCALER_%d) %s=0;\n" % (v, v, v, modulo, v))

    def lprint_opt(f, cond):
        if f is not None:
            p.with_cond(f, cond)

    for m in modules:
        for per in m.periodics:
            if per.autorun == "lock":
                lprint_opt(per.start, per.cond)
                if per.stop is not None:
                    sys.stderr.write("Warning: stop %s function will not be called\n" % per.stop)
            else:
                status = get_status_name(per.fname, m.name)
                p.out.write("\n")
                p.lprintf("if (%s == MODULES_START) {\n" % status)
                p.right()
                lprint_opt(per.start, per.cond)
                p.lprintf("%s = MODULES_RUN;\n" % status)
                p.left()
                p.lprintf("}\n")
                p.lprintf("if (%s == MODULES_STOP) {\n" % status)
                p.right()
                lprint_opt(per.stop, per.cond)
                p.lprintf("%s = MODULES_IDLE;\n" % status)
                p.left()
                p.lprintf("}\n")
    functions = sorted(fm, key=lambda x: x[1])
    p.out.write("\n")
    for (periodic, name, delay), (per, m) in functions:
        if name in names:
            _, f = get_period_and_freq(periodic.period_freq)
            if f == "(MODULES_FREQUENCY)":
                if periodic.autorun == "lock":
                    p.with_cond(periodic.call, periodic.cond)
                else:
                    p.lprintf("if (%s == MODULES_RUN) {\n" % get_status_name(periodic.fname, name))
                    p.right()
                    p.with_cond(periodic.call, periodic.cond)
                    p.left()
                    p.lprintf("}\n")
            else:
                run = "" if periodic.autorun == "lock" else " && %s == MODULES_RUN" % get_status_name(periodic.fname, name)
                p.lprintf("if (i%d == (uint32_t)(%sf * PRESCALER_%d)%s) {\n" % (m, "%f" % delay, m, run))
                p.right()
                p.with_cond(periodic.call, periodic.cond)
                p.left()
                p.lprintf("}\n")
    p.left()
    p.lprintf("}\n")


def print_periodic_functions(p, functions_modulo, modules):
    mot = modules_of_task(modules)
    for tm in mot:
        print_periodic(p, functions_modulo, tm)
    p.lprintf("\nstatic inline void modules_periodic_task(void) {\n")
    p.right()
    for t, _ in mot:
        p.lprintf("modules_%s_periodic_task();\n" % t)
    p.left()
    p.lprintf("}\n")


def print_event(p, task_modules):
    task, modules = task_modules
    p.lprintf("\nstatic inline void modules_%s_event_task(void) {\n" % task)
    p.right()
    for m in modules:
        for i in m.events:
            p.with_cond(i.ev, i.cond)
    p.left()
    p.lprintf("}\n")


def print_event_functions(p, modules):
    mot = modules_of_task(modules)
    for tm in mot:
        print_event(p, tm)
    p.lprintf("\nstatic inline void modules_event_task(void) {\n")
    p.right()
    for t, _ in mot:
        p.lprintf("modules_%s_event_task();\n" % t)
    p.left()
    p.lprintf("}\n")


def print_datalink_functions(p, modules):
    p.lprintf('\n#include "pprzlink/messages.h"\n')
    p.lprintf('\n#include "pprzlink/dl_protocol.h"\n')
    p.lprintf('\n#include "pprzlink/intermcu_msg.h"\n')
    p.lprintf('#include "generated/airframe.h"\n')
    p.lprintf("static inline void modules_parse_datalink(uint8_t msg_id __attribute__((unused)),\n"
              "                                          uint8_t class_id __attribute__((unused)),\n"
              "                                          struct link_device *dev __attribute__((unused)),\n"
              "                                          struct transport_tx *trans __attribute__((unused)),\n"
              "                                          uint8_t *buf __attribute__((unused))) {\n")
    p.right()
    msgs = Hashtbl(3, keymodel=option_key)
    for m in modules:
        for d in m.datalinks:
            if not msgs.mem(d.dl_class):
                msgs.add(d.dl_class, Hashtbl(15))
            c = msgs.find(d.dl_class)
            new_cb = (d.func, d.cond)
            if c.mem(d.message):
                c.replace(d.message, [new_cb] + c.find(d.message))
            else:
                c.add(d.message, [new_cb])
    p.lprintf("switch (class_id) {\n")
    p.right()
    for class_name, msg_tbl in msgs.items():
        cn = "datalink" if class_name is None else class_name
        p.lprintf("case DL_%s_CLASS_ID: {\n" % cn)
        p.right()
        p.lprintf("switch (msg_id) {\n")
        p.right()
        for msg_name, _ in msg_tbl.items():
            if msg_name != "*":
                p.lprintf("case DL_%s: {\n" % msg_name)
                p.right()
                for cb, cond in msg_tbl.find(msg_name):
                    p.with_cond(cb, cond)
                p.lprintf("break;\n")
                p.left()
                p.lprintf("}\n")
        p.lprintf("default: break;\n")
        p.left()
        p.lprintf("}\n")
        for msg_name, _ in msg_tbl.items():
            if msg_name == "*":
                for cb, cond in msg_tbl.find(msg_name):
                    p.with_cond(cb, cond)
        p.left()
        p.lprintf("  break;\n")
        p.lprintf("}\n")
    p.lprintf("default: break;\n")
    p.left()
    p.lprintf("}\n")
    p.left()
    p.lprintf("}\n")


def parse_modules(p, modules):
    print_headers(p, modules)
    print_function_freq(p, modules)
    fm = get_functions_modulos(modules)
    print_function_prescalers(p, fm)
    print_status(p, modules)
    p.out.write("\n")
    print_init_functions(p, modules)
    print_periodic_functions(p, fm, modules)
    print_event_functions(p, modules)
    p.out.write("\n")
    p.out.write("#ifdef MODULES_DATALINK_C\n")
    print_datalink_functions(p, modules)
    p.out.write("\n")
    p.out.write("#endif // MODULES_DATALINK_C\n")


H_NAME = "MODULES_H"


def generate(modules, xml_file, out_file):
    with open_out(out_file) as out:
        p = _P(out)
        begin_out(out, xml_file, H_NAME)
        define_out(out, "MODULES_IDLE ", "0")
        define_out(out, "MODULES_RUN  ", "1")
        define_out(out, "MODULES_START", "2")
        define_out(out, "MODULES_STOP ", "3")
        out.write("\n")
        out.write("#ifndef MODULES_FREQUENCY\n")
        out.write("#ifdef PERIODIC_FREQUENCY\n")
        out.write("#define MODULES_FREQUENCY PERIODIC_FREQUENCY\n")
        out.write("#else\n")
        out.write('#error "neither MODULES_FREQUENCY or PERIODIC_FREQUENCY are defined"\n')
        out.write("#endif\n")
        out.write("#endif\n")
        out.write("\n")
        print_modules_ids(p, modules)
        out.write("\n")
        out.write("#ifdef MODULES_C\n")
        out.write("#define EXTERN_MODULES\n")
        out.write("#else\n")
        out.write("#define EXTERN_MODULES extern\n")
        out.write("#endif\n")
        parse_modules(p, modules)
        finish_out(out, H_NAME)
