#!/usr/bin/python3
"""Joystick / gamepad to Ivy: reads an input device through SDL2 and sends
datalink or ground messages built from an XML description in conf/joystick/.

Python replacement of the OCaml `input2ivy` (same options and XML format,
including trims, exponent, deadband, limit, variables and the built-in
functions Scale, Fit, Bound, PprzMode, Hat*, JoystickID, IndexOfEnum,
IndexOfSetting and IndexOfBlock).

  input2ivy.py [-b bus] [-ac name] [-d index] [-v] [-id n] [-c] <file.xml>
"""

import ctypes
import ctypes.util
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "lib", "python"))

from pprzgen import xml_light as X, ext_xml, env  # noqa: E402
from pprzgen import fp_proc  # noqa: E402
from pprzgen.ocaml import concat, Failure, NotFound  # noqa: E402
from pprzground import ivybus  # noqa: E402
from pprzground.messages import MessageClass, value_of_string  # noqa: E402

CONF_DIR = concat(env.paparazzi_home, "conf")
MAX_INPUT, MIN_INPUT = 127, -127
TRIM_STEP = 0.2
PPRZ_THRESHOLD = MAX_INPUT // 2

DL = MessageClass("datalink")
G = MessageClass("ground")


def idiv(a, b):
    """OCaml integer division (truncates toward zero)."""
    if b == 0:
        raise ZeroDivisionError("Division_by_zero")
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


# ------------------------------------------------------------ SDL2 stick

class SdlStick:
    """sdl_stick.c on SDL2 (through ctypes): same scaling of the axes to
    [-127, 126], buttons as a bit field, a single hat."""
    AXIS_COUNT = BUTTON_COUNT = 32
    INPUT_DEV_MAX = 15
    SDL_INIT_JOYSTICK = 0x200
    SDL_QUIT, SDL_JOYAXISMOTION, SDL_JOYHATMOTION = 0x100, 0x600, 0x602
    SDL_JOYBUTTONDOWN, SDL_JOYBUTTONUP = 0x603, 0x604

    def __init__(self):
        name = ctypes.util.find_library("SDL2") or "libSDL2-2.0.so.0"
        self.sdl = ctypes.CDLL(name)
        self.sdl.SDL_JoystickOpen.restype = ctypes.c_void_p
        self.sdl.SDL_JoystickNumButtons.argtypes = [ctypes.c_void_p]
        self.sdl.SDL_JoystickNumHats.argtypes = [ctypes.c_void_p]
        self.sdl.SDL_JoystickNumAxes.argtypes = [ctypes.c_void_p]
        self.sdl.SDL_JoystickNameForIndex.restype = ctypes.c_char_p
        self.sdl.SDL_GetError.restype = ctypes.c_char_p
        self.event = ctypes.create_string_buffer(64)
        self.axis = [0] * self.AXIS_COUNT
        self.moved = [0] * self.AXIS_COUNT
        self.hat = 0
        self.buttons = 0
        self.nb_axis = self.nb_buttons = 0
        self.joystick = None

    def init_device(self, index):
        self.nb_buttons = self.nb_axis = 0
        j = self.sdl.SDL_JoystickOpen(index)
        if not j:
            return 1
        self.joystick = j
        self.nb_buttons = min(self.sdl.SDL_JoystickNumButtons(j), self.BUTTON_COUNT)
        self.nb_axis = min(self.sdl.SDL_JoystickNumAxes(j), self.AXIS_COUNT)
        name = self.sdl.SDL_JoystickNameForIndex(index)
        if name is not None:
            print('Input device name: "%s" on SDL device "%i"' % (name.decode("utf-8", "replace"), index), flush=True)
        return 0

    def init(self, index):
        self.sdl.SDL_SetHint(b"SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", b"1")
        if self.sdl.SDL_Init(self.SDL_INIT_JOYSTICK) < 0:
            print("Could not initialize SDL: %s." % self.sdl.SDL_GetError().decode(), flush=True)
            sys.exit(255)
        self.sdl.SDL_JoystickEventState(1)
        if not self.sdl.SDL_NumJoysticks():
            sys.stderr.write("Error: No joysticks attached!\n")
            self.sdl.SDL_Quit()
            return 1
        cnt = 0
        if self.init_device(index) != 0:
            print("Failed to open joystick at SDL device index %d, attempting to find a suitable joystick..." % index)
            for cnt in range(self.INPUT_DEV_MAX + 1):
                if cnt == self.INPUT_DEV_MAX or self.init_device(cnt) == 0:
                    break
            print("Found an alternative device!", flush=True)
        if cnt == self.INPUT_DEV_MAX:
            sys.stderr.write("ERROR: no suitable joystick found\n")
            self.sdl.SDL_Quit()
            return 1
        return 0

    def _poll(self, check):
        ev = self.event
        while self.sdl.SDL_PollEvent(ev):
            t = int.from_bytes(ev.raw[0:4], "little")
            if t in (self.SDL_JOYBUTTONDOWN, self.SDL_JOYBUTTONUP) and not check:
                b, state = ev.raw[12], ev.raw[13]
                if b < self.nb_buttons:
                    if state:
                        self.buttons |= (1 << b)
                    else:
                        self.buttons &= ~(1 << b)
            elif t == self.SDL_JOYHATMOTION and not check:
                if ev.raw[12] == 0:
                    self.hat = ev.raw[13]
            elif t == self.SDL_JOYAXISMOTION:
                a = ev.raw[12]
                v = int.from_bytes(ev.raw[16:18], "little", signed=True)
                if a < self.nb_axis:
                    self.axis[a] = ((v + 32768) * 254) // 65536 - 127
                    if check:
                        self.moved[a] = 1
            elif t == self.SDL_QUIT:
                print("Quitting...", flush=True)
                sys.exit(1)

    def check_axis(self):
        self._poll(True)
        return 1 if all(self.moved[i] for i in range(self.nb_axis)) else 0

    def read(self):
        self._poll(False)
        return self.nb_buttons, self.buttons, self.hat, self.axis[:self.nb_axis]


# ------------------------------------------------------------ description

class Axis:
    def __init__(self, index, deadband, limit, exponent, trim):
        self.index, self.deadband, self.limit, self.exponent, self.trim = index, deadband, limit, exponent, trim


class Button:
    def __init__(self, index):
        self.index = index


class Hat:
    def __init__(self, index):
        self.index = index


class Msg:
    def __init__(self, name, cls, fields, on_event, send_always, has_ac_id):
        self.msg_name, self.msg_class, self.fields = name, cls, fields
        self.on_event, self.send_always, self.has_ac_id = on_event, send_always, has_ac_id


class Var:
    def __init__(self, value, var_event):
        self.value, self.var_event = value, var_event


class Actions:
    def __init__(self, period_ms, inputs, messages, variables):
        self.period_ms, self.inputs, self.messages, self.variables = period_ms, inputs, messages, variables


class Input2Ivy:
    def __init__(self, joystick_id=129):
        self.joystick_id = joystick_id
        self.index_of_settings = {}
        self.index_of_blocks = {}
        self.trim_file_name = ""
        self.last_values = {}
        self.verbose = False

    # ---- conf
    @staticmethod
    def ac_id_of_name(ac_name):
        conf_xml = ext_xml.parse_file(concat(CONF_DIR, "conf.xml"))
        try:
            ac = ext_xml.child(conf_xml, "aircraft", select=lambda x: X.attrib(x, "name") == ac_name)
            return ext_xml.int_attrib(ac, "ac_id")
        except NotFound:
            if ac_name == "GCS":
                return 0
            raise Failure("A/C '%s' not found" % ac_name)

    def hash_index_of_settings(self, ac_name):
        xml = ext_xml.parse_file(concat(env.paparazzi_home, "var", "aircrafts", ac_name, "settings.xml"))
        index = [0]

        def loop(x):
            if X.tag(x) == "dl_settings":
                for c in X.children(x):
                    loop(c)
            else:
                self.index_of_settings[X.attrib(x, "var")] = index[0]
                index[0] += 1
        loop(ext_xml.child(xml, "dl_settings"))

    def hash_index_of_blocks(self, ac_name):
        dump = ext_xml.parse_file(concat(env.paparazzi_home, "var", "aircrafts", ac_name, "flight_plan.xml"))
        blocks = ext_xml.child(ext_xml.child(dump, "flight_plan"), "blocks")
        for b in X.children(blocks):
            self.index_of_blocks[X.attrib(b, "name")] = ext_xml.int_attrib(b, "no")

    # ---- parsing
    def eval_settings_and_blocks(self, field, expr):
        def loop(e):
            if e[0] == "call" and len(e[2]) == 1 and e[2][0][0] == "ident":
                name = e[2][0][1]
                if e[1] == "IndexOfEnum":
                    try:
                        return ("int", field.values.index(name))
                    except ValueError:
                        raise Failure("IndexOfEnum: unknown value '%s'" % name)
                if e[1] == "IndexOfSetting":
                    if name not in self.index_of_settings:
                        raise Failure("IndexOfSetting: unknown var '%s'" % name)
                    return ("int", self.index_of_settings[name])
                if e[1] == "IndexOfBlock":
                    if name not in self.index_of_blocks:
                        raise Failure("IndexOfBlock: unknown block '%s'" % name)
                    return ("int", self.index_of_blocks[name])
            if e[0] in ("call", "op"):
                return ("call", e[1], [loop(a) for a in e[2]])
            return e
        return loop(expr)

    @staticmethod
    def parse_input(xml):
        inputs = []
        for x in X.children(xml):
            name = X.attrib(x, "name")
            index = ext_xml.int_attrib(x, "index")
            t = X.tag(x)
            if t == "axis":
                def opt(f, a, d):
                    try:
                        return f(x, a)
                    except Exception:
                        return d
                inputs.append((name, Axis(index, opt(ext_xml.int_attrib, "deadband", 0),
                                          opt(ext_xml.float_attrib, "limit", 1.0),
                                          opt(ext_xml.float_attrib, "exponent", 0.0),
                                          opt(ext_xml.float_attrib, "trim", 0.0))))
            elif t == "button":
                inputs.append((name, Button(index)))
            elif t == "hat":
                inputs.append((name, Hat(index)))
            else:
                raise Failure("parse_input: unexepcted tag")
        return inputs

    @staticmethod
    def parse_value(s):
        return [fp_proc.parse_expression(sub.strip()) for sub in s.split(";")]

    def parse_msg_field(self, msg, field):
        name = X.attrib(field, "name")
        fd = next((f for f in msg.fields if f.name == name), None)
        if fd is None:
            print("parse_msg_field: field %s not found" % name, flush=True)
            raise Failure("field not found")
        value = [self.eval_settings_and_blocks(fd, e) for e in self.parse_value(X.attrib(field, "value"))]
        return (name, fd.type, value)

    def parse_msg(self, x):
        name, cls = X.attrib(x, "name"), X.attrib(x, "class")
        try:
            send_always = X.attrib(x, "send_always") == "true"
        except Exception:
            send_always = False
        if cls in ("datalink", "ground"):
            mc = DL if cls == "datalink" else G
            descr = mc.message_of_name(name)
            try:
                fields = [self.parse_msg_field(descr, f) for f in X.children(x)]
            except Failure as e:
                raise Failure("Couldn't parse message %s (%s)" % (name, e))
            has_ac_id = any(f.name == "ac_id" for f in descr.fields)
        elif cls in ("trim_plus", "trim_minus", "trim_save"):
            fields, has_ac_id = [], False
        else:
            raise Failure(cls)
        try:
            ev = self.parse_value(X.attrib(x, "on_event"))
            on_event = ev[0] if ev else None
        except Exception:
            on_event = None
        return Msg(name, cls, fields, on_event, send_always, has_ac_id)

    def parse_variables(self, xml):
        l = []
        kids = X.children(xml)
        for x in kids:
            if X.tag(x) != "var":
                continue
            name = X.attrib(x, "name")
            default = ext_xml.int_attrib(x, "default")
            if any(n == name for n, _ in l):
                raise Failure("Variable %s already declared" % name)
            events = []
            for s in kids:
                if ext_xml.tag_is(s, "set") and ext_xml.attrib_or_default(s, "var", "") == name:
                    value = ext_xml.int_attrib(s, "value")
                    ev = self.parse_value(X.attrib(s, "on_event"))
                    if ev:
                        events.insert(0, (value, ev[0]))
            l.insert(0, (name, Var(default, events)))
        return l

    @staticmethod
    def my_assoc(x, l):
        for k, v in l:
            if k == x:
                return v
        raise Failure("my_assoc: %s not found" % x)

    def parse_trim_file(self, trim_file, inputs):
        if os.path.exists(trim_file):
            trim = ext_xml.parse_file(trim_file)
            for x in X.children(trim):
                inp = self.my_assoc(ext_xml.attrib(x, "axis"), inputs)
                if not isinstance(inp, Axis):
                    raise Failure("No trim for buttons" if isinstance(inp, Button) else "No trim for hats")
                inp.trim = ext_xml.float_attrib(x, "value")

    def parse_descr(self, xml_file, trim_file):
        xml = ext_xml.parse_file(xml_file)
        inputs = self.parse_input(ext_xml.child(xml, "input"))
        messages_xml = ext_xml.child(xml, "messages")
        try:
            variables = self.parse_variables(ext_xml.child(xml, "variables"))
        except Exception:
            variables = []
        period_ms = int(1000.0 * ext_xml.float_attrib(messages_xml, "period"))
        messages = [self.parse_msg(m) for m in X.children(messages_xml)]
        self.parse_trim_file(trim_file, inputs)
        return Actions(period_ms, inputs, messages, variables)

    # ---- evaluation
    @staticmethod
    def eval_input(buttons, hat, axis, inp):
        if isinstance(inp, Axis):
            x = axis[inp.index]
            x = 0 if abs(x) < inp.deadband else x
            pow_value = float(x) ** 3.0 / float(MAX_INPUT) ** 2.0
            x = float(x) * (1.0 - inp.exponent) + pow_value * inp.exponent
            x = inp.limit * x
            x = int(x + inp.trim)
            return MAX_INPUT if x > MAX_INPUT else (MIN_INPUT if x < MIN_INPUT else x)
        if isinstance(inp, Button):
            return (buttons >> inp.index) & 1
        return hat

    def eval_call(self, f, a, hat):
        n = len(a)
        if f == "-" and n == 1:
            return -a[0]
        if n == 2:
            ops = {"-": lambda: a[0] - a[1], "+": lambda: a[0] + a[1], "*": lambda: a[0] * a[1],
                   "%": lambda: idiv(a[0], a[1]), "&&": lambda: a[0] & a[1], "||": lambda: a[0] | a[1],
                   "<": lambda: 1 if a[0] < a[1] else 0, ">": lambda: 1 if a[0] > a[1] else 0}
            if f in ops:
                return ops[f]()
        hats = {"HatCentered": 0, "HatUp": 1, "HatRight": 2, "HatRightUp": 3, "HatDown": 4,
                "HatRightDown": 6, "HatLeft": 8, "HatLeftUp": 9, "HatLeftDown": 12}
        if f in hats and n == 1:
            return 1 if hat == hats[f] else 0
        if f == "Scale" and n == 3:
            x, lo, hi = a
            return lo + idiv((x - MIN_INPUT) * (hi - lo), MAX_INPUT - MIN_INPUT)
        if f == "Fit" and n == 5:
            x, lo, hi, mi, ma = a
            v = mi + idiv((x - lo) * (ma - mi), hi - lo)
            return mi if v < mi else (ma if v > ma else v)
        if f == "Bound" and n == 3:
            x, lo, hi = a
            return lo if x < lo else (hi if x > hi else x)
        if f == "PprzMode" and n == 1:
            return 2 if a[0] > PPRZ_THRESHOLD else (0 if a[0] < -PPRZ_THRESHOLD else 1)
        if f == "JoystickID" and n == 0:
            return self.joystick_id
        raise Failure("eval_call: unknown function '%s'" % f)

    def eval_expr(self, buttons, hat, axis, inputs, variables, expr):
        def ev(e):
            k = e[0]
            if k == "ident":
                for n, inp in inputs:
                    if n == e[1]:
                        return self.eval_input(buttons, hat, axis, inp)
                for n, v in variables:
                    if n == e[1]:
                        return v.value
                raise Failure("eval_expr: %s not found" % e[1])
            if k == "int":
                return e[1]
            if k in ("call", "op"):
                return self.eval_call(e[1], [ev(x) for x in e[2]], hat)
            raise Failure("eval_expr: %s" % {"float": "float", "index": "index",
                                              "field": "Field", "deref": "deref"}.get(k, k))
        return ev(expr)

    def update_variables(self, inputs, buttons, hat, axis, variables):
        for _, var in variables:
            for value, expr in var.var_event:
                if self.eval_expr(buttons, hat, axis, inputs, variables, expr) != 0:
                    var.value = value

    def trim_adjust(self, axis_name, adjustment, inputs):
        inp = self.my_assoc(axis_name, inputs)
        if not isinstance(inp, Axis):
            raise Failure("No trim for buttons" if isinstance(inp, Button) else "No trim for hats")
        inp.trim += adjustment

    def trim_save(self, inputs):
        s = "<trims>"
        for name, inp in inputs:
            if isinstance(inp, Axis):
                s += "<trim axis='%s' value = '%f'/>" % (name, inp.trim)
            elif isinstance(inp, Button):
                print("%d" % inp.index, end="")
            else:
                print("hat", end="")
        s += "</trims>"
        with open(self.trim_file_name, "w", encoding="latin-1", newline="") as f:
            f.write(X.to_string_fmt(X.parse_string(s)))

    def execute_action(self, ac_id, inputs, buttons, hat, axis, variables, m):
        values = []
        for name, ftype, exprs in m.fields:
            v = [self.eval_expr(buttons, hat, axis, inputs, variables, e) for e in exprs]
            values.append((name, value_of_string(ftype, ",".join(str(i) for i in v))))
        on_event = True if m.on_event is None else \
            self.eval_expr(buttons, hat, axis, inputs, variables, m.on_event) != 0
        previous = self.last_values.get(m.msg_name, (False, []))
        if ((on_event, values) != previous or m.send_always) and on_event:
            c = m.msg_class
            if c == "datalink":
                vs = ([("ac_id", ("int", ac_id))] if m.has_ac_id else []) + values
                ivybus.message_send(DL, "input2ivy", m.msg_name, vs)
            elif c == "ground":
                vs = ([("ac_id", ("string", "%d" % ac_id))] if m.has_ac_id else []) + values
                ivybus.message_send(G, "input2ivy", m.msg_name, vs)
            elif c == "trim_plus":
                self.trim_adjust(m.msg_name, TRIM_STEP, inputs)
            elif c == "trim_minus":
                self.trim_adjust(m.msg_name, -TRIM_STEP, inputs)
            elif c == "trim_save":
                self.trim_save(inputs)
            else:
                raise Failure("execute_action: unknown class '%s'" % c)
        self.last_values[m.msg_name] = (on_event, values)

    def print_inputs(self, nb_buttons, buttons, hat, axis):
        s = "buttons: " + "".join("%d:%d " % (i, (buttons >> i) & 1) for i in range(nb_buttons))
        s += "\nhat: %d\naxis: " % hat
        s += "".join("%d:%d " % (i, self.eval_input(buttons, hat, axis, Axis(i, 0, 1.0, 0.0, 0.0)))
                     for i in range(len(axis)))
        print(s, flush=True)

    def execute_actions(self, stick, actions, ac_id):
        try:
            nb_buttons, buttons, hat, axis = stick.read()
            if self.verbose:
                self.print_inputs(nb_buttons, buttons, hat, axis)
            self.update_variables(actions.inputs, buttons, hat, axis, actions.variables)
            for m in actions.messages:
                self.execute_action(ac_id, actions.inputs, buttons, hat, axis, actions.variables, m)
        except Exception as e:
            sys.stderr.write("%s\n" % (e if isinstance(e, Failure) else repr(e)))
            sys.stderr.flush()


def usage():
    sys.stderr.write("""Usage:
  -b <ivy bus> Default is %s
  -ac <A/C name>
  -d <device index>
  -v Verbose mode (useful to identify the channels of an input device)
  -id Joystick ID, from 0-255.  Each joystick requires a unique ID in a multiple joystick configuration.
  -c Check all axis moved for correct initilization before starting normal behavior
  - <xml file of actions>
""" % ivybus.default_ivy_bus())


def main(argv=None, stick=None, stop=None):
    argv = sys.argv[1:] if argv is None else argv
    bus, device_index, ac_name, xml_descr, axis_check = ivybus.default_ivy_bus(), 0, "MYAC", "", False
    app = Input2Ivy()
    i = 0
    try:
        while i < len(argv):
            a = argv[i]
            if a in ("-b", "-ac", "-d", "-id", "-"):
                v = argv[i + 1]
                i += 1
                if a == "-b":
                    bus = v
                elif a == "-ac":
                    ac_name = v
                elif a == "-d":
                    device_index = int(v)
                elif a == "-id":
                    app.joystick_id = int(v)
                else:
                    xml_descr = v
            elif a == "-v":
                app.verbose = True
            elif a == "-c":
                axis_check = True
            elif a in ("-help", "--help"):
                usage()
                sys.exit(0)
            else:
                xml_descr = a
            i += 1
    except (IndexError, ValueError):
        usage()
        sys.exit(2)
    if xml_descr == "":
        usage()
        sys.exit(1)

    ac_id = app.ac_id_of_name(ac_name)
    if ac_id > 0:
        app.hash_index_of_settings(ac_name)
        app.hash_index_of_blocks(ac_name)
    print("Joystick ID (option -id): %u" % (app.joystick_id & 0xFFFFFFFF))
    print("Joystick SDL device index (option -d): %u" % (device_index & 0xFFFFFFFF), flush=True)

    xml_full = CONF_DIR + "/joystick/" + xml_descr
    app.trim_file_name = ".".join([xml_full, ac_name, "trim"])
    actions = app.parse_descr(xml_full, app.trim_file_name)

    if stick is None:
        stick = SdlStick()
        if stick.init(device_index) != 0:
            raise Failure("Error: cannot open device with SDL index %i\n" % device_index)

    ivybus.init("Paparazzi_joystick", "READY", bus)

    if axis_check:
        while stick.check_axis() != 1:
            time.sleep(0.1)

    stop = stop or threading.Event()
    period = actions.period_ms / 1000.0
    next_t = time.monotonic()
    while not stop.is_set():
        next_t += period
        app.execute_actions(stick, actions, ac_id)
        stop.wait(max(0.0, next_t - time.monotonic()))
    return app, actions


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        if isinstance(e, Failure):
            sys.stderr.write("Fatal error: exception Failure(\"%s\")\n" % e)
        elif isinstance(e, KeyError) and str(e.args[0]).startswith("Unknown_msg_name"):
            sys.stderr.write("Fatal error: exception PprzLink.%s\n" % e.args[0])
        else:
            sys.stderr.write("Fatal error: exception %r\n" % e)
        sys.stderr.flush()
        os._exit(2)
    ivybus.IvyStop()
    os._exit(0)
