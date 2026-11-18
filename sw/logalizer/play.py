#!/usr/bin/python3
"""Paparazzi log file player (replay).

Python replacement of the OCaml `play` (and `play-nox` with -nogui):
loads a server log (.log + .data), stores the logged configuration in
var/replay/ (so the server and the GCS can display the "replay<id>"
aircraft) and replays the telemetry on the Ivy bus at the logged pace
(speed follows the WORLD_ENV time_scale, e.g. from the environment
simulator). Optionally also writes binary PPRZ frames on a serial port.

  play.py [-b bus] [-d port] [-o] [-s baudrate] [-shfc] [-nogui] [file.log]
"""

import argparse
import bisect
import os
import re
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib", "python"))

from pprzgen import xml_light as X, ext_xml, env, geo  # noqa: E402
from pprzgen.ocaml import concat  # noqa: E402
from pprzground import ivybus  # noqa: E402
from pprzground.messages import MessageClass, float_assoc  # noqa: E402
from pprzground.transport import PprzTransport  # noqa: E402

REPLAY_DIR = concat(env.paparazzi_home, "var", "replay")
DUMP_FP = concat(env.paparazzi_src, "sw", "tools", "generators", "dump_flight_plan.py")


def write_xml(f, xml):
    os.makedirs(os.path.dirname(f), exist_ok=True)
    with open(f, "w", encoding="latin-1", newline="") as m:
        m.write(X.to_string_fmt(xml))


class Player:
    def __init__(self, o):
        self.o = o
        self.tm = MessageClass("telemetry")
        self.ground = MessageClass("ground")
        self.log = []
        self.speed = 1.0
        self.position = 0.0
        self.running = False
        self.generation = 0
        self.serial = None
        self.on_position = lambda t: None
        self.on_end = lambda: None

    # ------------------------------------------------------------ loading
    def store_conf(self, conf, acs):
        kids = []
        for x in X.children(conf):
            if not ext_xml.tag_is(x, "aircraft"):
                kids.append(x)
                continue
            if ext_xml.attrib(x, "ac_id") not in acs:
                continue
            ac_name = ext_xml.attrib(x, "name")
            ac_dir = concat(REPLAY_DIR, "var", "aircrafts", ac_name)

            def w(s):
                f = concat(REPLAY_DIR, "conf", ext_xml.attrib(x, s))
                write_xml(f, ext_xml.child(x, s))
                f2 = concat(ac_dir, "conf", ext_xml.attrib(x, s))
                write_xml(f2, ext_xml.child(x, s))
                return f2

            w("airframe")
            w("radio")
            try:
                xs = ext_xml.child(x, "generated_settings")
            except Exception:
                print("Replay: no settings for display", flush=True)
                xs = X.Element("settings", [], [])
            write_xml(concat(ac_dir, "settings.xml"), xs)
            write_xml(concat(REPLAY_DIR, "settings.xml"), xs)
            if any(isinstance(e, X.Element) and e.tag == "flight_plan" for e in X.children(x)):
                fp = w("flight_plan")
                os.makedirs(ac_dir, exist_ok=True)
                c = ["/usr/bin/python3", DUMP_FP, fp, concat(ac_dir, "flight_plan.xml")]
                if subprocess.run(c).returncode != 0:
                    raise RuntimeError(" ".join(c))
            else:
                write_xml(concat(ac_dir, "flight_plan.xml"), ext_xml.child(x, "dump"))
            kids.append(X.Element("aircraft", X.attribs(x), []))
        write_xml(concat(REPLAY_DIR, "conf", "conf.xml"), X.Element("conf", [], kids))

    def load(self, xml_file):
        xml = ext_xml.parse_file(xml_file)
        data_file = ext_xml.attrib(xml, "data_file")
        f = geo.find_file([os.path.dirname(xml_file) or "."], data_file)
        lines, acs = [], []
        for l in geo.read_compress(f).decode("latin-1").splitlines():
            m = re.match(r"^\s*([-+0-9.eE]+) +([^ ]+) +(.*)$", l)
            if not m:
                continue
            try:
                t = float(m.group(1))
            except ValueError:
                continue
            lines.append((t, m.group(2), m.group(3)))
            if m.group(2) not in acs:
                acs.append(m.group(2))
        self.log = lines
        self.store_conf(ext_xml.child(xml, "conf"), acs)
        write_xml(concat(REPLAY_DIR, "var", "messages.xml"), ext_xml.child(xml, "protocol"))

    def bounds(self):
        return (self.log[0][0], self.log[-1][0]) if self.log else (0.0, 1.0)

    # ------------------------------------------------------------ playing
    def send_one(self, t, ac, m):
        name = m.split(" ", 1)[0]
        if name in self.tm.by_name:
            ivybus.send("replay%s %s" % (ac, m))
            ivybus.send("time%s %f" % (ac, t))
            if self.serial is not None:
                try:
                    msg, vs = self.tm.values_of_string(m)
                    payload = self.tm.payload_of_values(msg.id, int(ac), 0, vs)
                    self.serial.write(PprzTransport.packet(payload))
                    self.serial.flush()
                except Exception:
                    pass
        if name in self.ground.by_name:
            ivybus.send("replay_ground %s" % m)

    def stop(self):
        self.running = False
        self.generation += 1

    def play(self):
        self.stop()
        if len(self.log) <= 1:
            return
        times = [l[0] for l in self.log]
        i0 = min(bisect.bisect_left(times, self.position), len(self.log) - 1)
        self.running = True
        gen = self.generation

        def loop():
            i = i0
            while self.running and gen == self.generation:
                t, ac, m = self.log[i]
                try:
                    self.send_one(t, ac, m)
                except Exception as e:
                    sys.stderr.write("%s\n" % e)
                self.position = t
                self.on_position(t)
                if i + 1 >= len(self.log):
                    self.running = False
                    self.on_end()
                    return
                dt = self.log[i + 1][0] - t
                time.sleep(max(0.0, dt / self.speed) if self.speed > 0 else 0.0)
                i += 1
        threading.Thread(target=loop, daemon=True).start()


def main():
    ap = argparse.ArgumentParser(prog="play", description="Paparazzi log player")
    ap.add_argument("-b", default=ivybus.default_ivy_bus(), help="<ivy bus>")
    ap.add_argument("-d", default="/dev/ttyUSB0", help="<port>")
    ap.add_argument("-o", action="store_true", help="Output binary messages on serial port")
    ap.add_argument("-s", default="9600", help="<baudrate>")
    ap.add_argument("-shfc", action="store_true", help="Enable UART hardware flow control (CTS/RTS)")
    ap.add_argument("-nogui", action="store_true", help="play the log once without window, then exit (play-nox)")
    ap.add_argument("file", nargs="?", default="")
    o = ap.parse_args()

    ivybus.init("Paparazzi_replay", "READY", o.b)
    p = Player(o)
    if o.file:
        p.load(o.file)
    if o.o:
        import serial
        p.serial = serial.Serial(o.d, int(o.s), rtscts=o.shfc)

    def world_env(_s, vs):
        try:
            p.speed = float_assoc("time_scale", vs)
        except Exception:
            pass
    ivybus.message_bind(p.ground, "WORLD_ENV", world_env)

    if o.nogui:
        done = threading.Event()
        p.on_end = done.set
        if p.log:
            p.position = 0.0
            p.play()
            done.wait()
        ivybus.IvyStop()
        os._exit(0)

    from PyQt5 import QtCore, QtGui, QtWidgets
    app = QtWidgets.QApplication(sys.argv[:1])
    w = QtWidgets.QMainWindow()
    w.setWindowTitle("Replay")
    icon = concat(env.paparazzi_home, "data/pictures/penguin_icon_rep.png")
    if os.path.exists(icon):
        w.setWindowIcon(QtGui.QIcon(icon))
    central = QtWidgets.QWidget()
    lay = QtWidgets.QVBoxLayout(central)
    slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
    slider.setRange(0, 1000)
    label = QtWidgets.QLabel("No log loaded")
    lay.addWidget(slider)
    lay.addWidget(label)
    w.setCentralWidget(central)
    w.resize(420, 90)

    class Sig(QtCore.QObject):
        pos = QtCore.pyqtSignal(float)
        end = QtCore.pyqtSignal()
    sig = Sig()
    p.on_position = sig.pos.emit
    p.on_end = sig.end.emit
    scale = [0.0, 1.0]

    def set_bounds(name):
        a, b = p.bounds()
        scale[0], scale[1] = a, (b - a) or 1.0
        slider.setValue(0)
        p.position = a
        w.setWindowTitle(os.path.basename(name))

    def show_pos(t):
        slider.blockSignals(True)
        slider.setValue(int((t - scale[0]) / scale[1] * 1000))
        slider.blockSignals(False)
        label.setText("t = %.1f s / %.1f s   speed x%g" % (t, scale[0] + scale[1], p.speed))

    sig.pos.connect(show_pos)
    sig.end.connect(lambda: slider.setEnabled(True))
    slider.valueChanged.connect(lambda v: setattr(p, "position", scale[0] + v / 1000.0 * scale[1]))

    def open_log():
        p.stop()
        slider.setEnabled(True)
        d = concat(env.paparazzi_home, "var", "logs")
        name, _ = QtWidgets.QFileDialog.getOpenFileName(w, "Open Log", d, "log (*.log)")
        if name:
            p.load(name)
            set_bounds(name)

    def play():
        slider.setEnabled(False)
        p.play()

    def stop():
        slider.setEnabled(True)
        p.stop()

    menu = w.menuBar().addMenu("File")
    for text, key, cb in (("Open Log", "Ctrl+O", open_log), ("Play", "Ctrl+X", play),
                          ("Stop", "Ctrl+S", stop), ("Quit", "Ctrl+Q", app.quit)):
        a = menu.addAction(text)
        a.setShortcut(QtGui.QKeySequence(key))
        a.triggered.connect(cb)
    w.show()
    if o.file:
        set_bounds(o.file)
        play()
    code = app.exec_()
    ivybus.IvyStop()
    os._exit(code)


if __name__ == "__main__":
    main()
