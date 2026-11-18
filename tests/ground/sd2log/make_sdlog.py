#!/usr/bin/python3
"""Build a synthetic SD-card log (pprzlog framing) from a server .data file,
with some corruption (junk bytes, bad checksums, foreign ac_id, bad source)
to exercise the error paths of sd2log.
  make_sdlog.py <in.data> <out.tlm>"""
import os
import random
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "sw", "lib", "python"))
from pprzground.messages import MessageClass  # noqa: E402


def frame(source, ts, pprz):
    body = bytes([source]) + struct.pack("<i", ts) + pprz
    m = bytearray([0x99, len(pprz)]) + body
    m.append(sum(m[1:]) & 0xFF)
    return bytes(m)


def main():
    tm, dl = MessageClass("telemetry"), MessageClass("datalink")
    rnd = random.Random(42)
    out = bytearray()
    n = 0
    for line in open(sys.argv[1], encoding="latin-1"):
        parts = line.split(" ", 2)
        if len(parts) < 3:
            continue
        t, ac, m = float(parts[0]), int(parts[1]), parts[2].rstrip("\n")
        try:
            msg, vs = tm.values_of_string(m)
        except Exception:
            continue
        pprz = tm.payload_of_values(msg.id, ac, 0, vs)
        out += frame(0, int(round(t * 1e4)), pprz)
        n += 1
        r = rnd.random()
        if r < 0.03:
            out += bytes(rnd.randrange(256) for _ in range(rnd.randrange(1, 20)))
        elif r < 0.05:
            f = bytearray(frame(0, int(t * 1e4), pprz))
            f[-1] ^= 0x5A
            out += f
        elif r < 0.06:
            out += frame(0, int(t * 1e4), tm.payload_of_values(msg.id, ac + 1, 0, vs))
        elif r < 0.07:
            out += frame(2, int(t * 1e4), pprz)
        elif r < 0.08:
            out += frame(1, int(t * 1e4), bytes([0, ac, 0, 2, 7]))  # datalink with wrong length
        if n % 50 == 0:
            m2, v2 = dl.values_of_string("SETTING 3 %d 1.5" % ac)
            out += frame(1, int(t * 1e4), dl.payload_of_values(m2.id, 0, ac, v2))
    with open(sys.argv[2], "wb") as f:
        f.write(out)
    print("%d frames, %d bytes" % (n, len(out)))


if __name__ == "__main__":
    main()
