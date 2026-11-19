#!/usr/bin/python3
"""Prints the results of the CMSIS-DSP benchmark (module cmsis_bench) sent by
an autopilot in PAYLOAD_FLOAT messages, as a table.

Start a Flight session for the benchmark aircraft (Data Link + Server), then:
  sw/tools/cmsis_bench_report.py [-b ivy_bus] [-id ac_id]
"""

import argparse
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib", "python"))

from pprzground import ivybus  # noqa: E402
from ivy.std_api import IvyBindMsg, IvyStop  # noqa: E402

TESTS = ["mat_mult 3x3", "mat_mult 6x6", "mat_mult 12x12",
         "mat_inv 3x3", "mat_inv 6x6", "mat_inv 12x12",
         "lowpass, 64 samples", "PID, 64 steps (update_pid_f)", "PID, 64 steps (same formula)"]


def report(ac, values):
    mhz = values[0]
    lines = ["", "CMSIS-DSP benchmark, aircraft %s, CPU %.0f MHz" % (ac, mhz),
             "%-30s %12s %12s %10s %10s %8s %12s" % ("test", "pprz cycles", "CMSIS cycles",
                                                  "pprz us", "CMSIS us", "speed-up", "max diff")]
    for i, name in enumerate(TESTS):
        p, c, d = values[1 + 3 * i: 4 + 3 * i]
        if c <= 0:
            continue
        lines.append("%-30s %12.0f %12.0f %10.2f %10.2f %7.2fx %12s" % (
            name, p, c, p / mhz, c / mhz, p / c, "n/a" if d < 0 else "%.2g" % d))
    lines.append("speed-up > 1: CMSIS is faster. 'PID (update_pid_f)' compares different algorithms "
                 "(Paparazzi's has anti-windup).")
    print("\n".join(lines), flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-b", default=ivybus.default_ivy_bus(), help="Ivy bus")
    ap.add_argument("-id", default=None, help="aircraft id (default: any)")
    ap.add_argument("-once", action="store_true", help="exit after the first report")
    o = ap.parse_args()
    got = threading.Event()

    def on_msg(_agent, ac, vals):
        if o.id is not None and ac != o.id:
            return
        try:
            values = [float(v) for v in vals.split(",")]
        except ValueError:
            return
        if len(values) != 1 + 3 * len(TESTS) or values[0] <= 0:
            return
        report(ac, values)
        got.set()

    ivybus.init("cmsis_bench_report", "READY", o.b, start=False)
    IvyBindMsg(on_msg, r"^(\S+) PAYLOAD_FLOAT (\S+)")
    ivybus.start()
    print("waiting for PAYLOAD_FLOAT from the benchmark (sent every 2 s, 5 s after boot)...", flush=True)
    try:
        while not (o.once and got.is_set()):
            got.wait(1.0)
    except KeyboardInterrupt:
        pass
    IvyStop()
    os._exit(0)


if __name__ == "__main__":
    main()
