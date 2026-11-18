#!/usr/bin/python3
"""Paparazzi Messages: live view of the messages on the Ivy bus.

PyQt5 replacement of the OCaml `messages` program, same options:
  -b <bus>  -c class[:sender] (default telemetry:*)  -timestamp  -force  -g WxH+X+Y

One tab per class (and per aircraft for "class:*"), one page per message
(shown on first arrival, sorted by name). Fields can be dragged onto the
Real-Time Plotter (text "sender:class:MSG:field[:index]:scale").
"""

import argparse
import os
import re
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "lib", "python"))

from PyQt5 import QtCore, QtGui, QtWidgets  # noqa: E402
from pprzgen import xml_light as X  # noqa: E402
from pprzgen import pprzlink as PL  # noqa: E402
from pprzground import ivybus  # noqa: E402
from pprzground.messages import MessageClass, formatted_string_of_value, string_of_value, int_of_value  # noqa: E402

DISPLAY_DELAY = 500   # ms between two refreshes
LED_DELAY = 500       # ms of green "led" after a message
HELP = "Drag-and-drop field on:\n\t- Real-Time Plotter to plot a curve\n\t- GCS map to display as a papget"


def alt_unit_coef(f):
    """PprzLink.alt_unit_coef_of_xml ~auto:"display" (as a string)."""
    try:
        return X.attrib(f, "alt_unit_coef")
    except Exception:
        pass
    try:
        u = X.attrib(f, "unit")
    except Exception:
        u = ""
    try:
        au = X.attrib(f, "alt_unit")
    except Exception:
        au = ""
    try:
        from pprzgen.ocaml import string_of_float
        return string_of_float(PL.scale_of_units(u, au, auto="display"))
    except Exception:
        return "1."


class FieldLabel(QtWidgets.QPushButton):
    def __init__(self, text, drag_text):
        super().__init__(text)
        self.drag_text = drag_text
        self.setFlat(True)
        self.setToolTip(HELP)
        self.setStyleSheet("text-align: left; padding: 0px 4px;")

    def mouseMoveEvent(self, e):
        if not (e.buttons() & QtCore.Qt.LeftButton):
            return
        text = self.drag_text()
        if text is None:
            return
        drag = QtGui.QDrag(self)
        mime = QtCore.QMimeData()
        mime.setText(text)
        drag.setMimeData(mime)
        drag.exec_(QtCore.Qt.CopyAction)


class MessagePage(QtWidgets.QWidget):
    def __init__(self, sender, class_name, msg_xml, cls):
        super().__init__()
        self.name = X.attrib(msg_xml, "name")
        self.msg = cls.message_of_name(self.name)
        self.time_since_last = 0
        self.dirty = False
        lay = QtWidgets.QGridLayout(self)
        lay.setVerticalSpacing(1)
        self.rows = []
        r = 0
        for f in X.children(msg_xml):
            if not (isinstance(f, X.Element) and f.tag == "field"):
                continue
            try:
                fname = X.attrib(f, "name")
                ftype = X.attrib(f, "type")
                unit = "(" + X.attrib(f, "unit") + ")" if _has(f, "unit") else ""
                fmt = X.attrib(f, "format") if _has(f, "format") else None
                literal = [v for v in X.attrib(f, "values").split("|") if v != ""] if _has(f, "values") else []
                coef = alt_unit_coef(f)
                alt = None
                if _has(f, "alt_unit"):
                    try:
                        alt = (float(coef), X.attrib(f, "alt_unit"))
                    except ValueError:
                        alt = None
                value_label = QtWidgets.QLabel("XXXX")
                value_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)

                def drag_text(fname=fname, ftype=ftype, coef=coef, vl=value_label):
                    v = vl.text().split(" ")[0]
                    if ftype.endswith("]"):
                        nb = len(v.split(","))
                        rng = "0-%d" % (nb - 1) if nb > 1 else "0"
                        i, ok = QtWidgets.QInputDialog.getText(self, "Index of value to drag",
                                                               "Index or range in the array ?", text=rng)
                        if not ok:
                            return None
                        return "%s:%s:%s:%s[%s]:%s" % (sender, class_name, self.name, fname, i, coef)
                    return "%s:%s:%s:%s:%s" % (sender, class_name, self.name, fname, coef)

                lay.addWidget(FieldLabel("%s %s %s: " % (ftype, fname, unit), drag_text), r, 0)
                lay.addWidget(value_label, r, 1)
                self.rows.append([fname, fmt, literal, alt, value_label, "XXXX"])
                r += 1
            except Exception:
                sys.stderr.write("Warning: Ignoring '%s'\n" % X.to_string(f))
        lay.setRowStretch(r, 1)
        lay.setColumnStretch(1, 1)

    def update_values(self, values):
        vd = dict(values)
        if len(values) != len(self.rows):
            sys.stderr.write("%s: expected %d args, got %d\n" % (self.name, len(self.rows), len(values)))
            return
        for row in self.rows:
            fname, fmt, literal, alt, _lbl, _ = row
            x = vd.get(fname.lower())
            if x is None:
                continue
            try:
                i = int_of_value(x)
                text = "%s (%d)" % (literal[i], i)
            except Exception:
                s = formatted_string_of_value(fmt, x) if fmt else string_of_value(x)
                if alt is not None:
                    try:
                        s = "%s (%f%s)" % (s, alt[0] * float(s), alt[1])
                    except ValueError:
                        pass
                text = s
            row[5] = text
        self.time_since_last = 0
        self.dirty = True

    def refresh(self):
        if self.dirty:
            for row in self.rows:
                if row[4].text() != row[5]:
                    row[4].setText(row[5])
            self.dirty = False


def _has(x, a):
    try:
        X.attrib(x, a)
        return True
    except Exception:
        return False


class ClassPage(QtWidgets.QTabWidget):
    """Messages of one class (optionally for one sender)."""

    def __init__(self, cls, class_xml, sender_name):
        super().__init__()
        self.setTabPosition(QtWidgets.QTabWidget.West)
        self.cls = cls
        self.class_name = cls.name
        self.sender_name = sender_name
        self.xml_by_name = {X.attrib(m, "name"): m for m in X.children(class_xml) if isinstance(m, X.Element)}
        self.pages = {}
        self.leds = {}

    def on_message(self, name, values):
        page = self.pages.get(name)
        if page is None:
            if name not in self.xml_by_name:
                return
            page = MessagePage(self.sender_name, self.class_name, self.xml_by_name[name], self.cls)
            self.pages[name] = page
            names = sorted(self.pages)
            scroll = QtWidgets.QScrollArea()
            scroll.setWidget(page)
            scroll.setWidgetResizable(True)
            self.insertTab(names.index(name), scroll, name)
        page.update_values(values)
        i = self._index(name)
        if i >= 0:
            self.tabBar().setTabTextColor(i, QtGui.QColor("#00a000"))
            self.leds[name] = time.time()

    def _index(self, name):
        for i in range(self.count()):
            if self.tabText(i) == name:
                return i
        return -1

    def tick_refresh(self):
        cur = self.currentWidget()
        for name, page in self.pages.items():
            i = self._index(name)
            if name in self.leds and time.time() - self.leds[name] > LED_DELAY / 1000.0:
                del self.leds[name]
                self.tabBar().setTabTextColor(i, self.palette().color(QtGui.QPalette.WindowText))
            if cur is not None and cur.widget() is page:
                page.refresh()

    def tick_second(self):
        for name, page in self.pages.items():
            page.time_since_last += 1
            i = self._index(name)
            if i >= 0:
                self.setTabText(i, name)
                self.setTabToolTip(i, "%2d s since last message" % page.time_since_last)


class Bridge(QtCore.QObject):
    message = QtCore.pyqtSignal(str, str, str)   # sender, msg name, text
    new_sender = QtCore.pyqtSignal(str, str)     # class spec ident, sender


class Window(QtWidgets.QWidget):
    def __init__(self, o):
        super().__init__()
        self.setWindowTitle("Messages")
        icon = os.path.join(os.environ.get("PAPARAZZI_HOME", ""), "data/pictures/penguin_icon_msg.png")
        if os.path.exists(icon):
            self.setWindowIcon(QtGui.QIcon(icon))
        self.o = o
        lay = QtWidgets.QVBoxLayout(self)
        self.tabs = QtWidgets.QTabWidget()
        lay.addWidget(self.tabs)
        self.bridge = Bridge()
        self.bridge.message.connect(self.dispatch)
        self.bridge.new_sender.connect(self.add_sender)
        self.pages = []  # (class name, sender or None, ClassPage)
        self.known = set()
        xml = PL.messages_xml()
        self.class_xml = {X.attrib(c, "name"): c for c in X.children(xml) if isinstance(c, X.Element)}
        classes = o.c or []
        for spec in ["telemetry:*"] + classes:
            parts = spec.split(":")
            cl = parts[0]
            if cl not in self.class_xml:
                raise SystemExit("Unknown messages class: %s" % cl)
            sender = parts[1] if len(parts) == 2 else None
            if sender == "*":
                self.wait_senders(spec, cl)
            else:
                self.add_class(spec, cl, sender)
        ts = "(?:[0-9]+\\.[0-9]+ )?" if o.timestamp else ""
        ivybus.bind_raw(lambda s, n, rest: self.bridge.message.emit(s, n, n + rest),
                        "^%s([^ ]+) +([A-Za-z0-9_]+)( .*|$)" % ts)
        t1 = QtCore.QTimer(self)
        t1.timeout.connect(lambda: [p.tick_refresh() for _, _, p in self.pages])
        t1.start(DISPLAY_DELAY)
        t2 = QtCore.QTimer(self)
        t2.timeout.connect(lambda: [p.tick_second() for _, _, p in self.pages])
        t2.start(1000)

    def wait_senders(self, spec, cl):
        cls = MessageClass(cl)
        names = set(cls.by_name)
        trigger = names if (self.o.force or cl != "telemetry") else {"ALIVE"}
        self.waiting = getattr(self, "waiting", [])
        self.waiting.append((spec, cl, trigger))

    def add_sender(self, spec, sender):
        cl = spec.split(":")[0]
        self.add_class(spec, cl, sender, label=cl + ":" + sender)

    def add_class(self, spec, cl, sender, label=None):
        cls = MessageClass(cl)
        page = ClassPage(cls, self.class_xml[cl], sender if sender is not None else "*")
        self.pages.append((cl, sender, page))
        self.tabs.addTab(page, label or (spec + ("" if sender is None else "")))

    def dispatch(self, sender, name, text):
        for spec, cl, trigger in getattr(self, "waiting", []):
            if name in trigger and (spec, sender) not in self.known:
                self.known.add((spec, sender))
                self.add_sender(spec, sender)
        for cl, s, page in self.pages:
            if s is not None and s != sender:
                continue
            if name not in page.xml_by_name:
                continue
            try:
                _m, values = page.cls.values_of_string(text)
            except Exception as e:
                sys.stderr.write("%s\n" % e)
                continue
            page.on_message(name, values)


def main():
    ap = argparse.ArgumentParser(prog="messages")
    ap.add_argument("-b", default=ivybus.default_ivy_bus(), help="<ivy bus>")
    ap.add_argument("-c", action="append", help="class name (class[:sender])")
    ap.add_argument("-timestamp", action="store_true")
    ap.add_argument("-force", action="store_true")
    ap.add_argument("-g", default="", help="<geometry> WxH+X+Y")
    o, unknown = ap.parse_known_args()
    for x in unknown:
        sys.stderr.write("WARNING: don't do anything with %s\n" % x)
    app = QtWidgets.QApplication(sys.argv[:1])
    ivybus.init("Paparazzi_messages", "READY", o.b)  # a space in the name breaks reception with ivy-python
    w = Window(o)
    m = re.match(r"(\d+)x(\d+)(?:\+(\d+)\+(\d+))?$", o.g)
    w.resize(int(m.group(1)), int(m.group(2))) if m else w.resize(420, 500)
    if m and m.group(3):
        w.move(int(m.group(3)), int(m.group(4)))
    w.show()
    code = app.exec_()
    # Ivy threads are not daemonic: stop the bus, then leave
    try:
        ivybus.IvyStop()
    except Exception:
        pass
    os._exit(code)


if __name__ == "__main__":
    main()
