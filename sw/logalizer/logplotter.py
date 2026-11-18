#!/usr/bin/python3
"""Paparazzi log plotter.

PyQt5/pyqtgraph replacement of the OCaml `logplotter`:
  logplotter.py [-export_csv] [-v] [file.log ...]

- open one or several server logs (.log + .data), browse aircraft /
  message / field (array elements as field[i]) and plot them against time;
- "Scale next by" (a or a+b) combined with the field's alt_unit_coef,
  constants, automatic or manual bounds, curve removal;
- exports: CSV (same format and preferences as the OCaml version, also in
  batch mode with -export_csv), KML path of the GPS positions, PNG image.
"""

import argparse
import calendar
import math
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib", "python"))

from pprzgen import xml_light as X, ext_xml, env, geo  # noqa: E402
from pprzgen.ocaml import concat, string_of_float  # noqa: E402
from pprzground.messages import MessageClass, string_of_value  # noqa: E402

LOGS_DIR = concat(env.paparazzi_home, "var", "logs")
GCONF = concat(env.paparazzi_home, "conf", "%gconf.xml")
verbose = False


# ---------------------------------------------------------------- helpers

def affine_transform(fmt):
    """Ocaml_tools.affine_transform: "a", "a+b", "a-b", "-a+b"... -> (a, b)"""
    s = re.sub(r"[ \t]+", "", fmt)
    parts = [p for p in re.split(r"([+-])", s) if p != ""]
    first_sign = 1.0 if (parts and (parts[0] not in "+-" or parts[0] == "+")) else (-1.0 if parts and parts[0] == "-" else 0.0)
    texts = [p for p in parts if p not in ("+", "-")]
    try:
        if len(texts) == 2:
            delims = [p for p in parts if p in ("+", "-")]
            second_sign = -1.0 if delims[-1] == "-" else 1.0
            return first_sign * float(texts[0]), second_sign * float(texts[1])
        if len(texts) == 1:
            return first_sign * float(texts[0]), 0.0
    except ValueError:
        pass
    return 1.0, 0.0


def alt_unit_coef(field_xml):
    try:
        return X.attrib(field_xml, "alt_unit_coef")
    except Exception:
        pass
    try:
        from pprzgen import pprzlink as PL
        u = X.attrib(field_xml, "unit") if _has(field_xml, "unit") else ""
        au = X.attrib(field_xml, "alt_unit") if _has(field_xml, "alt_unit") else ""
        return string_of_float(PL.scale_of_units(u, au))
    except Exception:
        return "1."


def _has(x, a):
    try:
        X.attrib(x, a)
        return True
    except Exception:
        return False


def pprz_float(v):
    k = v[0]
    if k in ("int", "int32", "int64", "float"):
        return float(v[1])
    if k == "char":
        return float(ord(v[1]))
    try:
        return float(v[1])
    except Exception:
        return math.nan


class Log:
    """A loaded log: per aircraft, per message, per field -> [(t, value)]"""

    def __init__(self, xml_file):
        self.file = xml_file
        xml = ext_xml.parse_file(xml_file)
        data_file = ext_xml.attrib(xml, "data_file")
        self.protocol = ext_xml.child(xml, "protocol")
        class_name = "telemetry"
        for c in X.children(self.protocol):
            if isinstance(c, X.Element) and _has(c, "name") and X.attrib(c, "name") == "telemetry_ap":
                class_name = "telemetry_ap"
        self.cls = MessageClass(class_name, self.protocol)
        self.class_xml = [c for c in X.children(self.protocol)
                          if isinstance(c, X.Element) and _has(c, "name") and X.attrib(c, "name") == class_name][0]
        d = os.path.dirname(xml_file) or "."
        try:
            f = geo.find_file([d], data_file)
        except Exception:
            sys.stderr.write("File '%s' not found\n" % data_file)
            data_file = os.path.splitext(os.path.basename(xml_file))[0] + ".data"
            sys.stderr.write("Trying with '%s'\n" % data_file)
            f = geo.find_file([d], data_file)
        self.acs = {}   # ac -> {msg name: {field: [(t, v)]}}
        self.raw = {}   # ac -> [(t, msg name, values)]
        for l in geo.read_compress(f).decode("latin-1").splitlines():
            m = re.match(r"^\s*([-+0-9.eE]+) +([^ ]+) +(.*)$", l)
            if not m:
                continue
            try:
                t = float(m.group(1))
                msg, vs = self.cls.values_of_string(m.group(3))
            except Exception as e:
                if verbose:
                    sys.stderr.write("%s\n" % e)
                continue
            ac = m.group(2)
            fields = self.acs.setdefault(ac, {}).setdefault(msg.name, {})
            for fname, v in vs:
                if v[0] == "array":
                    for i, s in enumerate(v[1]):
                        fields.setdefault("%s[%d]" % (fname, i), []).append((t, pprz_float(s)))
                else:
                    fields.setdefault(fname, []).append((t, pprz_float(v)))
            self.raw.setdefault(ac, []).append((t, msg.name, vs))
        self.name = os.path.splitext(os.path.basename(xml_file))[0]

    def field_xml(self, msg_name, field):
        f = field.split("[")[0]
        for m in X.children(self.class_xml):
            if isinstance(m, X.Element) and X.attrib(m, "name") == msg_name:
                for fx in X.children(m):
                    if isinstance(fx, X.Element) and fx.tag == "field" and X.attrib(fx, "name").lower() == f.lower():
                        return fx
        return None


# ---------------------------------------------------------------- CSV export

def date_of_log_filename(filename):
    m = re.match(r"^(\d\d)_(\d\d)_(\d\d)__(\d\d)_(\d\d)_(\d\d)", os.path.basename(filename))
    if not m:
        sys.stderr.write("Error parsing filename to get date (%s)\nUsing current time\n" % filename)
        return time.time()
    y, mo, d, h, mi, s = [int(x) for x in m.groups()]
    return time.mktime((2000 + y, mo, d, h, mi, s, 0, 0, -1))


def format_time(t):
    tm = time.gmtime(t)
    sec = float(tm.tm_sec) + math.modf(t)[0]
    return "%02d:%02d:%2.3f" % (tm.tm_hour, tm.tm_min, sec)


def get_last_geo_pos(lookup):
    if lookup("GPS", "mode") != "":
        utm = geo.Utm(utm_x=float(lookup("GPS", "utm_east")) / 100.0, utm_y=float(lookup("GPS", "utm_north")) / 100.0,
                      utm_zone=int(lookup("GPS", "utm_zone")))
        return geo.of_utm(utm)
    if lookup("INS_REF", "ecef_x0") != "" and lookup("ROTORCRAFT_FP", "east") != "":
        g = lambda m, f: float(lookup(m, f))
        ecef0 = [g("INS_REF", "ecef_x0") / 100.0, g("INS_REF", "ecef_y0") / 100.0, g("INS_REF", "ecef_z0") / 100.0]
        ned = [g("ROTORCRAFT_FP", "north") / 256.0, g("ROTORCRAFT_FP", "east") / 256.0, -g("ROTORCRAFT_FP", "up") / 256.0]
        return geo.geo_of_ecef(geo.ecef_of_ned(ecef0, ned))[0]
    raise ValueError("No geo pos found")


def read_preferences():
    try:
        xml = ext_xml.parse_file(GCONF)
        for e in X.children(xml):
            if isinstance(e, X.Element) and e.tag == "entry" and X.attrib(e, "name") == "to_export":
                return [tuple(p.split(":")) for p in X.attrib(e, "value").split(";") if p.count(":") == 1]
    except Exception:
        pass
    return []


def save_preferences(fields):
    value = ";".join("%s:%s" % mf for mf in fields)
    try:
        xml = ext_xml.parse_file(GCONF)
    except Exception:
        xml = X.Element("gconf", [], [])
    kids = [e for e in X.children(xml) if not (isinstance(e, X.Element) and e.tag == "entry"
                                                and _has(e, "name") and X.attrib(e, "name") == "to_export")]
    entry = X.Element("entry", [("name", "to_export"), ("value", value), ("application", "log plotter")], [])
    with open(GCONF, "w", encoding="latin-1", newline="") as f:
        f.write(ext_xml.to_string_fmt(X.Element(X.tag(xml), X.attribs(xml), [entry] + kids)) + "\n")


def export_values(fields_to_export, data, timestamp, filename, sep="tab", export_geo_pos=True):
    """fields_to_export: [(msg, field)] (OCaml order: last checked first)."""
    sep = "\t" if sep == "tab" else sep
    save_preferences(fields_to_export)
    log_date = date_of_log_filename(filename)
    last = {}
    with open(filename, "w", encoding="latin-1", newline="") as f:
        f.write("Time%sUTC" % sep)
        if export_geo_pos:
            f.write("%sGPS_lat(deg)%sGPS_long(deg)" % (sep, sep))
        for m, fld in fields_to_export:
            f.write("%s%s:%s" % (sep, m, fld))
        f.write("\n")

        def lookup(m, fld):
            v = last.get((m, fld.lower()))
            return "" if v is None else string_of_value(v)

        def print_last(t):
            ok = [True]
            buf = ["%.3f%s%s" % (t, sep, format_time(log_date + t))]
            if export_geo_pos:
                try:
                    g = get_last_geo_pos(lookup)
                    buf.append("%s%.9f%s%.9f" % (sep, geo.rad_to_deg(g.posn_lat), sep, geo.rad_to_deg(g.posn_long)))
                except Exception:
                    ok[0] = False
                    buf.append("%sN/A%sN/A" % (sep, sep))
            for m, fld in fields_to_export:
                v = lookup(m, fld)
                if v == "":
                    ok[0] = False
                buf.append(sep + v)
            if ok[0]:
                f.write("".join(buf) + "\n")

        tnext = data[0][0] if data else 0.0
        for t, msg, vs in data:
            if timestamp[0] == "period":
                if t >= tnext:
                    print_last(tnext)
                    tnext = tnext + timestamp[1]
            for fname, v in vs:
                last[(msg, fname)] = v
            if timestamp[0] == "msg" and timestamp[1] == msg:
                print_last(t)


# ---------------------------------------------------------------- KML

def gps_path(log, ac):
    msgs = log.acs.get(ac, {})
    out = []
    if "GPS" in msgs:
        f = msgs["GPS"]
        for (t, x), (_, y), (_, z), (_, a) in zip(f["utm_east"], f["utm_north"], f["utm_zone"], f["alt"]):
            z, a = int(z), a / 1000.0
            if z != 0 and a > 0.0:
                try:
                    out.append((t, geo.of_utm(geo.Utm(x / 100.0, y / 100.0, z)), a))
                except Exception:
                    pass
    elif "GPS_INT" in msgs:
        f = msgs["GPS_INT"]
        for (t, la), (_, lo), (_, a) in zip(f["lat"], f["lon"], f["hmsl"]):
            a = a / 1000.0
            if la != 0.0:
                out.append((t, geo.make_geo_deg(la / 1e7, lo / 1e7), a))
    return out


def write_kml(filename, name, path, tmin, tmax):
    coords = " ".join("%.6f,%.6f,%f" % (geo.rad_to_deg(p.posn_long), geo.rad_to_deg(p.posn_lat), a)
                      for t, p, a in path if tmin <= t < tmax)
    with open(filename, "w") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="http://www.opengis.net/kml/2.2">\n'
                "<Document><name>%s</name><Placemark><name>%s</name><LineString><altitudeMode>absolute"
                "</altitudeMode><coordinates>%s</coordinates></LineString></Placemark></Document></kml>\n"
                % (name, name, coords))


# ---------------------------------------------------------------- GUI

def gui(files):
    from PyQt5 import QtCore, QtGui, QtWidgets
    import pyqtgraph as pg

    app = QtWidgets.QApplication(sys.argv[:1])
    win = QtWidgets.QMainWindow()
    win.setWindowTitle("Log plotter")
    icon = concat(env.paparazzi_home, "data/pictures/penguin_icon_log.png")
    if os.path.exists(icon):
        win.setWindowIcon(QtGui.QIcon(icon))
    split = QtWidgets.QSplitter()
    tree = QtWidgets.QTreeWidget()
    tree.setHeaderLabels(["Log / aircraft / message / field"])
    right = QtWidgets.QWidget()
    rl = QtWidgets.QVBoxLayout(right)
    bar = QtWidgets.QHBoxLayout()
    auto = QtWidgets.QCheckBox("Auto Scale")
    auto.setChecked(True)
    cst = QtWidgets.QLineEdit()
    cst.setPlaceholderText("constant")
    cst.setMaximumWidth(80)
    factor = QtWidgets.QLineEdit("1+0")
    factor.setMaximumWidth(70)
    factor.setToolTip("Scale next curve (e.g. 0.0174 to convert deg in rad, 57.3 rad in deg, 1.8+32 Celsius in Fahrenheit)")
    for w_ in (auto, QtWidgets.QLabel("Constant:"), cst, QtWidgets.QLabel("Scale next by"), factor):
        bar.addWidget(w_)
    bar.addStretch(1)
    rl.addLayout(bar)
    plot = pg.PlotWidget()
    plot.setBackground("w")
    plot.showGrid(x=True, y=True)
    legend = plot.addLegend(offset=(1, 1))
    plot.setLabel("bottom", "time (s)")
    rl.addWidget(plot)
    curves_list = QtWidgets.QListWidget()
    curves_list.setMaximumHeight(110)
    curves_list.setToolTip("Double-click a curve to remove it")
    rl.addWidget(curves_list)
    split.addWidget(tree)
    split.addWidget(right)
    split.setSizes([300, 800])
    win.setCentralWidget(split)
    win.resize(1150, 650)
    logs = []
    items = {}
    colors = ["#1f77b4", "#d62728", "#2ca02c", "#ff7f0e", "#9467bd", "#8c564b", "#e377c2", "#17becf"]

    def add_log(name):
        try:
            lg = Log(name)
        except Exception as e:
            QtWidgets.QMessageBox.warning(win, "Log plotter", "Cannot load %s: %s" % (name, e))
            return
        logs.append(lg)
        top = QtWidgets.QTreeWidgetItem([lg.name])
        tree.addTopLevelItem(top)
        for ac in sorted(lg.acs):
            aci = QtWidgets.QTreeWidgetItem(["aircraft %s" % ac])
            top.addChild(aci)
            msgs = sorted(lg.acs[ac], key=lambda n: lg.cls.message_of_name(n).id)
            for mn in msgs:
                mi = QtWidgets.QTreeWidgetItem([mn])
                aci.addChild(mi)
                for fn in sorted(lg.acs[ac][mn]):
                    fi = QtWidgets.QTreeWidgetItem([fn])
                    fi.setData(0, QtCore.Qt.UserRole, (len(logs) - 1, ac, mn, fn))
                    mi.addChild(fi)
            exp_csv = QtWidgets.QTreeWidgetItem(["Export CSV..."])
            exp_csv.setData(0, QtCore.Qt.UserRole, ("csv", len(logs) - 1, ac))
            exp_kml = QtWidgets.QTreeWidgetItem(["Export KML path..."])
            exp_kml.setData(0, QtCore.Qt.UserRole, ("kml", len(logs) - 1, ac))
            aci.addChild(exp_csv)
            aci.addChild(exp_kml)
        top.setExpanded(True)

    def add_curve(li, ac, mn, fn):
        lg = logs[li]
        fx = lg.field_xml(mn, fn)
        a, b = affine_transform(factor.text())
        a2, b2 = affine_transform(alt_unit_coef(fx) if fx is not None else "1.")
        a, b = a * a2, a * b2 + b
        name = "%s:%s:%s:%s:%s" % (lg.name, ac, mn, fn, factor.text())
        if name in items:
            return
        vals = sorted(lg.acs[ac][mn][fn])
        pen = pg.mkPen(colors[len(items) % len(colors)], width=1.5)
        c = plot.plot([t for t, _ in vals], [v * a + b for _, v in vals], pen=pen, name=name)
        items[name] = c
        it = QtWidgets.QListWidgetItem(name)
        it.setForeground(QtGui.QColor(colors[(len(items) - 1) % len(colors)]))
        curves_list.addItem(it)

    def on_double(item, _col):
        d = item.data(0, QtCore.Qt.UserRole)
        if d is None:
            return
        if d[0] == "csv":
            export_dialog(logs[d[1]], d[2])
        elif d[0] == "kml":
            lg = logs[d[1]]
            fn, _ = QtWidgets.QFileDialog.getSaveFileName(win, "Export KML path", concat(LOGS_DIR, lg.name + ".kml"), "kml (*.kml)")
            if fn:
                (x0, x1), _ = plot.viewRange()
                write_kml(fn, "%s:%s" % (lg.name, d[2]), gps_path(lg, d[2]),
                          -sys.float_info.max if auto.isChecked() else x0, sys.float_info.max if auto.isChecked() else x1)
        else:
            add_curve(*d)

    def remove_curve(it):
        name = it.text()
        if name in items:
            plot.removeItem(items.pop(name))
            try:
                legend.removeItem(name)
            except Exception:
                pass
        curves_list.takeItem(curves_list.row(it))

    def add_cst():
        try:
            v = float(cst.text())
        except ValueError:
            return
        name = "constant %g" % v
        if name not in items:
            items[name] = plot.addLine(y=v, pen=pg.mkPen("k", style=QtCore.Qt.DashLine))
            curves_list.addItem(QtWidgets.QListWidgetItem(name))

    def export_dialog(lg, ac):
        dlg = QtWidgets.QDialog(win)
        dlg.setWindowTitle("Export CSV: %s aircraft %s" % (lg.name, ac))
        lay = QtWidgets.QVBoxLayout(dlg)
        t = QtWidgets.QTreeWidget()
        t.setHeaderLabels(["Message / field (check to export)"])
        prefs = read_preferences()
        names = []
        for _t, mn, _v in lg.raw.get(ac, []):
            if mn not in names:
                names.append(mn)
        for mn in [n for n in [X.attrib(m, "name") for m in X.children(lg.class_xml) if isinstance(m, X.Element)] if n in names]:
            mi = QtWidgets.QTreeWidgetItem([mn])
            t.addTopLevelItem(mi)
            for fx in X.children(lg.class_xml):
                pass
            fxml = [m for m in X.children(lg.class_xml) if isinstance(m, X.Element) and X.attrib(m, "name") == mn][0]
            for fx in X.children(fxml):
                if isinstance(fx, X.Element) and fx.tag == "field":
                    fn = X.attrib(fx, "name")
                    fi = QtWidgets.QTreeWidgetItem([fn])
                    fi.setCheckState(0, QtCore.Qt.Checked if (mn, fn) in prefs else QtCore.Qt.Unchecked)
                    mi.addChild(fi)
                    if (mn, fn) in prefs:
                        mi.setExpanded(True)
        lay.addWidget(t)
        form = QtWidgets.QFormLayout()
        ts = QtWidgets.QComboBox()
        ts.addItems(["Periodic"] + names)
        period = QtWidgets.QLineEdit("0.25")
        sep = QtWidgets.QComboBox()
        sep.addItems(["tab", ";", ","])
        ll = QtWidgets.QCheckBox("Export GPS lat/long")
        ll.setChecked(True)
        ts.currentTextChanged.connect(lambda s: period.setEnabled(s == "Periodic"))
        form.addRow("Timestamp", ts)
        form.addRow("Period (s)", period)
        form.addRow("Separator", sep)
        form.addRow(ll)
        lay.addLayout(form)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        lay.addWidget(bb)
        bb.rejected.connect(dlg.reject)

        def save():
            fields = []
            for i in range(t.topLevelItemCount()):
                mi = t.topLevelItem(i)
                for j in range(mi.childCount()):
                    if mi.child(j).checkState(0) == QtCore.Qt.Checked:
                        fields.insert(0, (mi.text(0), mi.child(j).text(0)))
            fn, _ = QtWidgets.QFileDialog.getSaveFileName(dlg, "Save Values", concat(LOGS_DIR, "%s:%s.csv" % (lg.name, ac)))
            if not fn:
                return
            stamp = ("period", float(period.text())) if ts.currentText() == "Periodic" else ("msg", ts.currentText())
            export_values(fields, lg.raw.get(ac, []), stamp, fn, sep.currentText(), ll.isChecked())
            dlg.accept()
        bb.accepted.connect(save)
        dlg.resize(420, 520)
        dlg.exec_()

    tree.itemDoubleClicked.connect(on_double)
    curves_list.itemDoubleClicked.connect(remove_curve)
    cst.returnPressed.connect(add_cst)
    auto.toggled.connect(lambda on: plot.enableAutoRange() if on else plot.disableAutoRange())

    m = win.menuBar().addMenu("File")

    def open_log():
        names, _ = QtWidgets.QFileDialog.getOpenFileNames(win, "Open Log", LOGS_DIR, "log (*.log)")
        for n in names:
            add_log(n)

    def screenshot():
        fn, _ = QtWidgets.QFileDialog.getSaveFileName(win, "Save screenshot", concat(LOGS_DIR, "plot.png"), "png (*.png)")
        if fn:
            plot.grab().save(fn)

    for text, key, cb in (("Open Log", "Ctrl+O", open_log), ("Save screenshot", "Ctrl+S", screenshot),
                          ("Quit", "Ctrl+Q", app.quit)):
        a = m.addAction(text)
        a.setShortcut(QtGui.QKeySequence(key))
        a.triggered.connect(cb)
    for f in files:
        add_log(f)
    win.show()
    win.add_log, win.add_curve, win.items, win.plot = add_log, add_curve, items, plot  # for tests
    return app, win


def main():
    global verbose
    ap = argparse.ArgumentParser(prog="logplotter", description="Paparazzi log plotter")
    ap.add_argument("-export_csv", action="store_true",
                    help="Export in CSV in batch mode according to saved preferences (conf/%%gconf.xml)")
    ap.add_argument("-v", action="store_true", help="Verbose")
    ap.add_argument("logs", nargs="*")
    o = ap.parse_args()
    verbose = o.v
    if o.export_csv:
        prefs = read_preferences()
        for f in o.logs:
            lg = Log(f)
            for ac, data in lg.raw.items():
                # OCaml batch export: dialog defaults (Periodic 0.25 s, tab, lat/long), file "<log>:<ac>.csv"
                export_values(list(prefs), data, ("period", 0.25), concat(LOGS_DIR, "%s:%s.csv" % (lg.name, ac)))
        return
    app, _w = gui(o.logs)
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
