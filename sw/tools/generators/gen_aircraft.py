#!/usr/bin/env python3
"""Paparazzi aircraft code generator (Python replacement of gen_aircraft.out).

Usage: gen_aircraft.py -name AC -target TARGET [-conf conf.xml] [-all | -airframe
       -flight_plan -settings -radio -telemetry -autopilot] [-ac_dir D] [-conf_dir D]
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "lib", "python"))

from pprzgen.gen_aircraft import entry  # noqa: E402

if __name__ == "__main__":
    entry()
