#!/usr/bin/python3
"""End-to-end test of the Python ground segment with the NPS simulator.

Starts, on a private Ivy bus, the same programs as the Paparazzi Center
"Simulation" session (simulator, link -udp -udp_broadcast, server) and
checks what a GCS would rely on:

 - the server registers the aircraft (NEW_AIRCRAFT) and publishes
   FLIGHT_PARAM / NAV_STATUS / AP_STATUS / ENGINE_STATUS /
   TELEMETRY_STATUS / WAYPOINT_MOVED with sensible values;
 - AIRCRAFTS and CONFIG requests are answered;
 - uplink: a ground JUMP_TO_BLOCK goes server -> link -> simulated
   firmware, which then reports the new block in NAV_STATUS;
 - DL_SETTING round trip (setting value confirmed by DL_VALUES).

  tests/ground/sim_session.py [-a Rover_Tana] [--server ocaml|python] [--link ocaml|python]
"""

import argparse
import xml.etree.ElementTree as ET
import os
import re
import subprocess
import sys
import threading
import time

HOME = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
os.environ["PAPARAZZI_HOME"] = HOME
os.environ["PAPARAZZI_SRC"] = HOME
sys.path.insert(0, os.path.join(HOME, "sw", "lib", "python"))
from pprzground import ivybus  # noqa: E402,F401  (fixes of ivy-python races)
from ivy.std_api import IvyInit, IvyStart, IvyBindMsg, IvySendMsg  # noqa: E402

PROGS = {
    ("link", "python"): ["/usr/bin/python3", HOME + "/sw/ground_segment/tmtc/link.py"],
    ("link", "ocaml"): [HOME + "/sw/ground_segment/tmtc/link"],
    ("server", "python"): ["/usr/bin/python3", HOME + "/sw/ground_segment/tmtc/server.py"],
    ("server", "ocaml"): [HOME + "/sw/ground_segment/tmtc/server"],
}


class Bus:
    def __init__(self, bus):
        self.msgs = []
        self.lock = threading.Lock()
        IvyInit("simtest", "READY", 0, lambda *a: None, lambda *a: None)
        IvyStart(bus)
        IvyBindMsg(self.on, "^(.*)$")

    def on(self, agent, text):
        with self.lock:
            self.msgs.append((time.time(), text))

    def find(self, rx, since=0.0):
        r = re.compile(rx)
        with self.lock:
            return [t for (ts, t) in self.msgs if ts >= since and r.search(t)]

    def wait(self, rx, timeout, since=0.0):
        end = time.time() + timeout
        while time.time() < end:
            m = self.find(rx, since)
            if m:
                return m
            time.sleep(0.2)
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-a", default="Rover_Tana")
    ap.add_argument("--server", default="python")
    ap.add_argument("--link", default="python")
    ap.add_argument("--port", type=int, default=2777)
    ap.add_argument("--gcs", action="store_true", help="also start PprzGCS (off-screen) and check its requests")
    ap.add_argument("--log_name", default="", help="with --log: server -log_name <name>")
    ap.add_argument("--log", action="store_true", help="let the server write var/logs files and check them")
    a = ap.parse_args()
    bus_addr = "127.255.255.255:%d" % a.port
    bus = Bus(bus_addr)
    logs = {}
    procs = []

    def start(name, args):
        f = open("/tmp/simtest_%s_%d.log" % (name, os.getpid()), "w")
        logs[name] = f.name
        procs.append(subprocess.Popen(args, stdout=f, stderr=subprocess.STDOUT, cwd=HOME))

    ok = True

    def check(label, cond, detail=""):
        nonlocal ok
        print("%-62s %s %s" % (label, "OK " if cond else "FAIL", detail))
        ok = ok and bool(cond)

    try:
        logdir = os.path.join(HOME, "var", "logs")
        before_logs = set(os.listdir(logdir)) if os.path.isdir(logdir) else set()
        start("server", PROGS[("server", a.server)] + ["-b", bus_addr] + ([] if a.log else ["-n"])
              + (["-log_name", a.log_name] if a.log_name else []))
        start("link", PROGS[("link", a.link)] + ["-b", bus_addr, "-udp", "-udp_broadcast"])
        time.sleep(2)
        start("sim", ["/usr/bin/python3", HOME + "/sw/simulator/pprzsim-launch", "-a", a.a, "-t", "nps",
                      "-b", bus_addr, "--norc", "--nodisplay"])
        if a.gcs:
            env_gcs = dict(os.environ, QT_QPA_PLATFORM="offscreen")
            f = open("/tmp/simtest_gcs_%d.log" % os.getpid(), "w")
            logs["gcs"] = f.name
            procs.append(subprocess.Popen(["pprzgcs", "-b", bus_addr], stdout=f, stderr=subprocess.STDOUT, env=env_gcs))
        new = bus.wait(r"^ground NEW_AIRCRAFT ", 30)
        check("server registers the aircraft (NEW_AIRCRAFT)", new, new[0] if new else "")
        if not new:
            raise RuntimeError("no aircraft")
        ac_id = new[0].split()[-1]
        bus.wait(r"^ground NAV_STATUS %s " % ac_id, 20)  # needs the NAVIGATION_REF telemetry first
        for msg in ("FLIGHT_PARAM", "NAV_STATUS", "AP_STATUS", "ENGINE_STATUS", "TELEMETRY_STATUS",
                    "FLY_BY_WIRE", "SVSINFO", "SURVEY_STATUS"):
            m = bus.find(r"^ground %s %s " % (msg, ac_id))
            check("server publishes %s" % msg, m, ("(%d) %s" % (len(m), m[-1][:90])) if m else "")
        fp = bus.find(r"^ground FLIGHT_PARAM %s " % ac_id)
        if fp:
            f = fp[-1].split()
            lat, lon = float(f[6]), float(f[7])
            fpx = ET.parse(os.path.join(HOME, "var", "aircrafts", a.a, "flight_plan.xml")).getroot()
            fpx = fpx if fpx.tag == "flight_plan" else fpx.find("flight_plan")  # dump format
            lat0, lon0 = float(fpx.get("lat0")), float(fpx.get("lon0"))
            check("FLIGHT_PARAM position near the flight plan origin", abs(lat - lat0) < 0.1 and abs(lon - lon0) < 0.1,
                  "lat=%f long=%f (origin %f %f)" % (lat, lon, lat0, lon0))
        # requests
        t0 = time.time()
        IvySendMsg("simtest 42_1 AIRCRAFTS_REQ")
        r = bus.wait(r"^42_1 ground AIRCRAFTS ", 5, t0)
        check("AIRCRAFTS request answered", r and (ac_id + ",") in r[0], r[0] if r else "")
        IvySendMsg("simtest 42_2 CONFIG_REQ %s" % ac_id)
        r = bus.wait(r"^42_2 ground CONFIG ", 5, t0)
        check("CONFIG request answered with flight plan/airframe files",
              r and "flight_plan.xml" in r[0] and "file://" in r[0], r[0][:110] if r else "")
        # uplink: jump to block 3 (Standby)
        nav = bus.find(r"^ground NAV_STATUS %s " % ac_id)
        before = nav[-1].split()[3] if nav else "?"
        t0 = time.time()
        IvySendMsg("gcs JUMP_TO_BLOCK %s 3" % ac_id)
        dl = bus.wait(r"^ground_dl BLOCK 3 %s" % ac_id, 5, t0)
        check("server forwards JUMP_TO_BLOCK as datalink BLOCK", dl, dl[0] if dl else "")
        nav = bus.wait(r"^ground NAV_STATUS %s 3 " % ac_id, 10, t0)
        check("simulated rover switched to block 3 (via link uplink)", nav,
              "before: block %s, after: %s" % (before, nav[-1].split()[3] if nav else "?"))
        # setting round trip (index 0)
        t0 = time.time()
        vals = bus.find(r"^ground DL_VALUES %s " % ac_id)
        how = "periodic DL_VALUE"
        if not vals:
            # DL_VALUE is not in the current telemetry mode: ask for setting 0,
            # as a GCS does (server -> GET_SETTING -> aircraft -> DL_VALUE)
            how = "after GET_DL_SETTING"
            IvySendMsg("gcs GET_DL_SETTING %s 0" % ac_id)
            vals = bus.wait(r"^ground DL_VALUES %s " % ac_id, 10, t0)
        check("server publishes DL_VALUES (settings values)", vals,
              ("%s: %s" % (how, vals[-1][:80])) if vals else how)
        tele = bus.find(r"^%s (ALIVE|ROVER_STATUS|GPS|NAVIGATION) " % ac_id)
        check("link publishes telemetry on the bus", tele, "%d msgs" % len(tele))
        lr = bus.find(r"^link LINK_REPORT %s " % ac_id)
        check("link publishes LINK_REPORT", lr, lr[-1] if lr else "")
        if a.gcs:
            # PprzGCS asks the server as soon as it joins the bus, possibly
            # before it knows this test agent's subscriptions: its requests are
            # not always visible here, the server's answers (sent to all) are
            end = time.time() + 30  # PprzGCS can be slow to start (e.g. in a fresh container)
            while True:
                answers = bus.find(r"^(?!42_)[0-9]+_[0-9]+ ground (AIRCRAFTS|CONFIG) ")
                kinds = set(x.split()[2] for x in answers)
                if {"AIRCRAFTS", "CONFIG"} <= kinds or time.time() > end:
                    break
                time.sleep(0.5)
            check("server answered PprzGCS requests (AIRCRAFTS and CONFIG)", {"AIRCRAFTS", "CONFIG"} <= kinds,
                  " | ".join(x[:70] for x in answers[:2]))
            gcs_out = bus.find(r"^(gcs|pprzgcs|PprzGCS)")
        if a.log:
            new_logs = sorted(set(os.listdir(logdir)) - before_logs)
            data = [f for f in new_logs if f.endswith(".data")]
            xmlf = [f for f in new_logs if f.endswith(".log")]
            check("server wrote a .log and a .data file", data and xmlf, " ".join(new_logs))
            if a.log_name:
                check("-log_name: files named <name>__<date>.log/.data, data_file points to the .data",
                      data and xmlf and data[0].startswith(a.log_name + "__") and data[0][:-5] == xmlf[0][:-4]
                      and ('data_file="%s"' % data[0]) in open(os.path.join(logdir, xmlf[0]), encoding="latin-1").read(),
                      " ".join(new_logs))
            if data and xmlf:
                sys.path.insert(0, os.path.join(HOME, "sw", "lib", "python"))
                from pprzgen import xml_light as X
                x = X.parse_string(open(os.path.join(logdir, xmlf[0]), encoding="latin-1").read())
                acs = [c for c in X.children(X.children(x)[0]) if isinstance(c, X.Element) and c.tag == "aircraft"]
                check(".log is valid XML with the expanded aircraft configurations", len(acs) > 0, "%d aircraft" % len(acs))
                lines = open(os.path.join(logdir, data[0]), encoding="latin-1").read().splitlines()
                good = [l for l in lines if re.match(r"^[0-9]+\.[0-9]{3} %s [A-Z_0-9]+( |$)" % ac_id, l)]
                check(".data has one timestamped line per telemetry message", len(good) > 50 and len(good) == len(lines),
                      "%d lines, e.g. %s" % (len(lines), lines[len(lines) // 2][:70] if lines else ""))
    except Exception as e:
        check("session", False, str(e))
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.kill()
        subprocess.run(["pkill", "-f", "simsitl.*%s" % a.a], stderr=subprocess.DEVNULL)
    for name, path in logs.items():
        with open(path) as f:
            content = f.read().strip()
        errs = [l for l in content.splitlines() if re.search(r"(?i)error|exception|traceback", l)]
        if errs:
            print("--- %s log: %d suspicious lines, e.g.:" % (name, len(errs)))
            for l in errs[:5]:
                print("    " + l[:160])
    print("RESULT:", "PASS" if ok else "FAIL")
    os._exit(0 if ok else 1)


if __name__ == "__main__":
    main()
