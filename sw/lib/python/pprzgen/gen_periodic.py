"""Port of sw/tools/generators/gen_periodic.ml: generates periodic_telemetry.h"""

from . import xml_light as X
from . import ext_xml, env
from .xml2h import define_out, open_out
from .ocaml import Hashtbl, NotFound, float_of_string


class _Printer:
    def __init__(self, out):
        self.out = out
        self.margin = 0

    def right(self):
        self.margin += 2

    def left(self):
        self.margin -= 2

    def lprintf(self, s):
        self.out.write(" " * self.margin)
        self.out.write(s)


def output_modes(p, process_name, telem_type, modes):
    out = p.out
    for mode in modes:
        mode_name = ext_xml.attrib(mode, "name")
        p.lprintf("if (telemetry_mode_%s == TELEMETRY_MODE_%s_%s) {\n" % (process_name, process_name, mode_name))
        p.right()
        found_modulos = Hashtbl(15)
        idx = 0
        phase = 0.0
        messages = []
        for x in X.children(mode):
            period = ext_xml.attrib(x, "period")
            try:
                _p = float_of_string(ext_xml.attrib(x, "phase"))
                _phase = _p / 65536.0 if _p > 0.95 else _p
            except Exception:
                phase = phase + 0.1
                if phase > 0.9:
                    phase = 0.0
                _phase = phase
            try:
                messages.append(((x, _phase), (period, found_modulos.find(period))))
            except NotFound:
                idx += 1
                v = "i%d" % idx
                p.lprintf("static uint32_t %s = 0; %s++; if (%s>= (uint32_t)(TELEMETRY_FREQUENCY*%s)) %s=0;\n" % (v, v, v, period, v))
                found_modulos.add(period, idx)
                messages.append(((x, _phase), (period, idx)))
        if messages:
            p.lprintf("uint8_t j;\n")
        messages = sorted(messages, key=lambda m: m[1][0])
        for (message, _phase), (per, i) in reversed(messages):
            message_name = ext_xml.attrib(message, "name")
            p.lprintf("if (i%d == (uint32_t)(TELEMETRY_FREQUENCY*%s*%s)) {\n" % (i, per, "%f" % _phase))
            p.right()
            p.lprintf("for (j = 0; j < TELEMETRY_NB_CBS; j++) {\n")
            p.right()
            p.lprintf("if (telemetry->cbs[TELEMETRY_%s_MSG_%s_IDX].slots[j] != NULL)\n" % (telem_type, message_name))
            p.right()
            p.lprintf("telemetry->cbs[TELEMETRY_%s_MSG_%s_IDX].slots[j](trans, dev);\n" % (telem_type, message_name))
            p.left()
            p.lprintf("else break;\n")
            p.left()
            p.lprintf("}\n")
            out.write("#if USE_PERIODIC_TELEMETRY_REPORT\n")
            p.lprintf("if (j == 0) periodic_telemetry_err_report(TELEMETRY_PROCESS_%s, telemetry_mode_%s, %s_MSG_ID_%s);\n"
                      % (process_name, process_name, telem_type, message_name))
            out.write("#endif\n")
            p.left()
            p.lprintf("}\n")
        p.left()
        p.lprintf("}\n")


def print_message_table(out, xml):
    telemetry_types = Hashtbl(2)
    for process in X.children(xml):
        telem_type = ext_xml.attrib_or_default(process, "type", "pprz").upper()
        if not telemetry_types.mem(telem_type):
            telemetry_types.add(telem_type, Hashtbl(15))
        messages = telemetry_types.find(telem_type)
        for mode in X.children(process):
            for msg in X.children(mode):
                n = ext_xml.attrib(msg, "name")
                if not messages.mem(n):
                    messages.add(n, None)
    for telem_type, messages in telemetry_types.items():
        out.write("/* Periodic telemetry messages of type %s */\n" % telem_type)
        nb = 0
        for n, _ in messages.items():
            define_out(out, "TELEMETRY_%s_MSG_%s_IDX" % (telem_type, n), "%d" % nb)
            nb += 1
        out.write("#define TELEMETRY_%s_NB_MSG %d\n" % (telem_type, nb))
        out.write("\n#define TELEMETRY_%s_MSG_NAMES { \\\n" % telem_type)
        for n, _ in messages.items():
            out.write('  "%s", \\\n' % n)
        out.write("}\n\n")
        out.write("#define TELEMETRY_%s_CBS { \\\n" % telem_type)
        for n, _ in messages.items():
            out.write("  {.id=%s_MSG_ID_%s, .slots={ NULL }}, \\\n" % (telem_type, n))
        out.write("}\n\n")


def print_process_send(p, xml):
    out = p.out
    out.write("\n/* Number of processes in telemetry file */\n")
    define_out(out, "TELEMETRY_PROCESS_NB", str(len(X.children(xml))))
    for p_id, process in enumerate(X.children(xml)):
        process_name = ext_xml.attrib(process, "name")
        telem_type = ext_xml.attrib_or_default(process, "type", "pprz").upper()
        modes = X.children(process)
        out.write("\n/* Periodic telemetry (type %s): %s process */\n" % (telem_type, process_name))
        define_out(out, "TELEMETRY_PROCESS_%s" % process_name, str(p_id))
        for i, mode in enumerate(modes):
            name = ext_xml.attrib(mode, "name")
            define_out(out, "TELEMETRY_MODE_%s_%s" % (process_name, name), str(i))
            for x in X.children(mode):
                n = ext_xml.attrib(x, "name")
                per = ext_xml.attrib(x, "period")
                define_out(out, "PERIOD_%s_%s_%d" % (n, process_name, i), "(%s)" % per)
        up = process_name.upper()
        out.write("\n/* Functions for %s process */\n" % process_name)
        out.write("#ifdef PERIODIC_C_%s\n" % up)
        out.write("#ifndef TELEMETRY_MODE_%s\n" % up)
        out.write("#define TELEMETRY_MODE_%s 0\n" % up)
        out.write("#endif\n")
        out.write("uint8_t telemetry_mode_%s = TELEMETRY_MODE_%s;\n" % (process_name, up))
        out.write("#else /* PERIODIC_C_%s not defined (general header) */\n" % up)
        out.write("extern uint8_t telemetry_mode_%s;\n" % process_name)
        out.write("#endif /* PERIODIC_C_%s */\n" % up)
        p.lprintf("static inline void periodic_telemetry_send_%s(struct periodic_telemetry *telemetry, struct transport_tx *trans, struct link_device *dev) {\n" % process_name)
        p.right()
        output_modes(p, process_name, telem_type, modes)
        p.left()
        p.lprintf("}\n")


def generate(telemetry, out_file):
    with open_out(out_file) as out:
        out.write("/* This file has been generated by gen_periodic */\n")
        out.write("/* Version %s */\n" % env.get_paparazzi_version())
        out.write("/* Please DO NOT EDIT */\n\n")
        out.write("#ifndef _VAR_PERIODIC_H_\n")
        out.write("#define _VAR_PERIODIC_H_\n\n")
        out.write('#include "std.h"\n')
        out.write('#include "generated/airframe.h"\n')
        out.write('#include "modules/datalink/telemetry_common.h"\n')
        out.write("\n")
        out.write("#ifndef TELEMETRY_FREQUENCY\n")
        out.write("#ifdef PERIODIC_FREQUENCY\n")
        out.write("#define TELEMETRY_FREQUENCY PERIODIC_FREQUENCY\n")
        out.write("#else\n")
        out.write('#error "neither TELEMETRY_FREQUENCY or PERIODIC_FREQUENCY are defined"\n')
        out.write("#endif\n")
        out.write("#endif\n")
        out.write("\n")
        print_message_table(out, telemetry.xml)
        print_process_send(_Printer(out), telemetry.xml)
        out.write("#endif // _VAR_PERIODIC_H_\n")
