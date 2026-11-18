#!/usr/bin/env python3
"""Dump the expanded version of a flight plan (Python replacement of
dump_flight_plan.out).

Usage: dump_flight_plan.py <flight_plan.xml> <output.xml>
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "lib", "python"))

from pprzgen import flight_plan as Fp, gen_flight_plan  # noqa: E402
from pprzgen.ocaml import Failure  # noqa: E402


def main(argv):
    if len(argv) < 3:
        raise Failure("Dump FP: please choose a flight plan XML and an output file")
    fp = Fp.from_file(argv[1])
    gen_flight_plan.generate(fp, fp.filename, argv[2], dump=True)


if __name__ == "__main__":
    try:
        main(sys.argv)
    except Failure as e:
        sys.stderr.write('Fatal error: exception Failure("%s")\n' % e)
        sys.exit(2)
