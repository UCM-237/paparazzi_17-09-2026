#!/usr/bin/python3
"""Gaia, the environment simulator: time scale, wind and GPS availability.

Python replacement of the OCaml `gaia`. Broadcasts WORLD_ENV (ground class)
every 5 s and on every change, and answers WORLD_ENV_REQ from simulators.

  gaia.py [-b bus] [-t time_scale] [-w wind_speed] [-d wind_dir] [-u updraft] [-g] [-nogui]
"""

import argparse
import math
import os
import signal
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib", "python"))

from pprzgen import env  # noqa: E402
from pprzgen.ocaml import concat  # noqa: E402
from pprzground import ivybus  # noqa: E402
from pprzground.messages import MessageClass  # noqa: E402

MY_ID = "gaia"
SENDING_PERIOD = 5.0  # s


class World:
    def __init__(self, o):
        self.lock = threading.Lock()
        self.time_scale = o.t
        self.wind_speed = o.w
        self.wind_dir = o.d
        self.wind_up = o.u
        self.ir_contrast = 266.0
        self.gps_off = o.g
        self.ground = MessageClass("ground")

    def values(self, *_):
        with self.lock:
            wind_dir_rad = math.pi / 2.0 - math.radians(self.wind_dir)
            return [("wind_east", ("float", -self.wind_speed * math.cos(wind_dir_rad))),
                    ("wind_north", ("float", -self.wind_speed * math.sin(wind_dir_rad))),
                    ("wind_up", ("float", self.wind_up)),
                    ("ir_contrast", ("float", self.ir_contrast)),
                    ("time_scale", ("float", self.time_scale)),
                    ("gps_availability", ("int", 0 if self.gps_off else 1))]

    def set(self, **kw):
        with self.lock:
            for k, v in kw.items():
                setattr(self, k, v)
        self.send()

    def send(self):
        ivybus.message_send(self.ground, MY_ID, "WORLD_ENV", self.values())


def main():
    ap = argparse.ArgumentParser(prog="gaia", description="Paparazzi environment simulator")
    ap.add_argument("-b", default=ivybus.default_ivy_bus(), help="Bus")
    ap.add_argument("-t", type=float, default=1.0, help="Set time scale (default: 1.0)")
    ap.add_argument("-w", type=float, default=0.0, help="Set wind speed (0-30m/s)")
    ap.add_argument("-d", type=float, default=0.0, help="Set wind direction 0-359 deg")
    ap.add_argument("-u", type=float, default=0.0, help="Set wind updraft (-10 to 10m/s)")
    ap.add_argument("-g", action="store_true", help="Turn off GPS")
    ap.add_argument("-nogui", action="store_true", help="no window (values from the command line only)")
    o, extra = ap.parse_known_args()
    for x in extra:
        sys.stderr.write("Warning: Don't do anythig with %s\n" % x)

    w = World(o)
    ivybus.init("Paparazzi_gaia", "READY", o.b)
    ivybus.message_answerer(w.ground, MY_ID, "WORLD_ENV", w.values)

    if o.nogui:
        stop = threading.Event()
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        while not stop.wait(SENDING_PERIOD):
            w.send()
        ivybus.IvyStop()
        os._exit(0)

    from PyQt5 import QtCore, QtGui, QtWidgets
    app = QtWidgets.QApplication(sys.argv[:1])
    win = QtWidgets.QWidget()
    win.setWindowTitle("Gaia")
    icon = concat(env.paparazzi_home, "data", "pictures", "penguin_icon.png")
    if os.path.exists(icon):
        win.setWindowIcon(QtGui.QIcon(icon))
    form = QtWidgets.QFormLayout(win)

    ts = QtWidgets.QDoubleSpinBox()
    ts.setRange(0.5, 10.0)
    ts.setSingleStep(0.5)
    ts.setDecimals(1)
    ts.setValue(min(max(o.t, 0.5), 10.0))
    form.addRow("Time scale:", ts)

    def slider(lo, hi, scale, value):
        s = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        s.setRange(int(round(lo * scale)), int(round(hi * scale)))
        s.setValue(int(round(min(max(value, lo), hi) * scale)))
        lab = QtWidgets.QLabel()
        lab.setMinimumWidth(40)
        fmt = "%.0f" if scale == 1 else "%.1f"
        lab.setText(fmt % (s.value() / scale))
        s.valueChanged.connect(lambda v: lab.setText(fmt % (v / scale)))
        box = QtWidgets.QHBoxLayout()
        box.addWidget(lab)
        box.addWidget(s, 1)
        return s, box

    wd, box = slider(0, 359, 1, o.d)
    form.addRow("Wind dir:", box)
    ws, box = slider(0, 30, 10, o.w)
    form.addRow("Wind speed:", box)
    wu, box = slider(-10, 10, 10, o.u)
    form.addRow("Wind up:", box)
    gps = QtWidgets.QPushButton("GPS OFF")
    gps.setCheckable(True)
    gps.setChecked(o.g)
    form.addRow(gps)

    ts.valueChanged.connect(lambda v: w.set(time_scale=v))
    wd.valueChanged.connect(lambda v: w.set(wind_dir=float(v)))
    ws.valueChanged.connect(lambda v: w.set(wind_speed=v / 10.0))
    wu.valueChanged.connect(lambda v: w.set(wind_up=v / 10.0))
    gps.toggled.connect(lambda v: w.set(gps_off=v))

    timer = QtCore.QTimer()
    timer.timeout.connect(w.send)
    timer.start(int(SENDING_PERIOD * 1000))
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    wake = QtCore.QTimer()  # lets Python run its signal handlers
    wake.timeout.connect(lambda: None)
    wake.start(200)
    win.resize(320, 0)
    win.show()
    code = app.exec_()
    ivybus.IvyStop()
    os._exit(code)


if __name__ == "__main__":
    main()
