#!/usr/bin/python3
"""Differential test of the data link: OCaml `link` vs Python `link.py`.

A deterministic byte stream (every telemetry message with random values,
plus corrupted frames and junk) is written into a FIFO used as the modem
device; everything the link publishes on a private Ivy bus is recorded and
the two recordings are compared (LINK_REPORT timing fields excluded).
Uplink: datalink messages are sent on the bus and the bytes written by each
link to the "modem" are compared.

  tests/ground/link_diff.py [--transport pprz|xbee] [--seed N] [--count N]
"""

import argparse
import os
import random
import re
import struct
import subprocess
import sys
import tempfile
import threading
import time

HOME = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(HOME, "sw", "lib", "python"))
os.environ.setdefault("PAPARAZZI_HOME", HOME)
os.environ.setdefault("PAPARAZZI_SRC", HOME)

from pprzground.messages import MessageClass, TYPES  # noqa: E402
from pprzground import transport as T  # noqa: E402
from ivy.std_api import IvyInit, IvyStart, IvyBindMsg, IvySendMsg, IvyStop  # noqa: E402

LINKS = {
    "ocaml": [os.path.join(HOME, "sw/ground_segment/tmtc/link")],
    "python": ["/usr/bin/python3", os.path.join(HOME, "sw/ground_segment/tmtc/link.py")],
}


def rand_scalar(rng, t):
    if t == "uint8":
        return ("int", rng.randrange(256))
    if t == "int8":
        return ("int", rng.randrange(-127, 128))
    if t == "uint16":
        return ("int", rng.randrange(65536))
    if t == "int16":
        return ("int", rng.randrange(-32768, 32768))
    if t == "int32":
        return ("int32", rng.randrange(-2**31, 2**31))
    if t == "uint32":
        return ("int64", rng.randrange(2**32))
    if t in ("int64", "uint64"):
        return ("int64", rng.randrange(-2**62, 2**62))
    if t == "float":
        c = rng.random()
        if c < 0.1:
            return ("float", 0.0)
        if c < 0.2:
            return ("float", float(rng.randrange(-1000, 1000)))
        return ("float", struct.unpack("<f", struct.pack("<f", rng.uniform(-1e5, 1e5)))[0])
    if t == "double":
        return ("float", rng.uniform(-1e7, 1e7))
    if t == "char":
        return ("char", chr(rng.randrange(33, 127)))
    if t == "string":
        n = rng.randrange(0, 12)
        return ("string", "".join(chr(rng.randrange(33, 127)) for _ in range(n)))
    raise ValueError(t)


def rand_value(rng, ft):
    if ft[0] == "scalar":
        return rand_scalar(rng, ft[1])
    t = ft[1]
    n = ft[2] if ft[0] == "fixed" else rng.randrange(0, 6)
    if t == "char":
        return ("array", [rand_scalar(rng, "char") for _ in range(n)])
    return ("array", [rand_scalar(rng, t) for _ in range(n)])


def downlink_stream(rng, tm, count, xbee):
    out = bytearray()
    msgs = sorted(tm.by_id.values(), key=lambda m: m.id)
    for k in range(count):
        msg = msgs[k % len(msgs)] if k < len(msgs) else rng.choice(msgs)
        ac = rng.choice([1, 2, 7])
        vals = [(f.name, rand_value(rng, f.type)) for f in msg.fields]
        try:
            payload = tm.payload_of_values(msg.id, ac, 0, vals)
        except Exception:
            continue
        if len(payload) + 4 >= 256:
            continue
        if xbee and len(payload) > 100:
            continue  # XBee radio limit
        if xbee:
            frame = T.api_tx16(0, payload) if rng.random() < 0.5 else bytes([0x81, 0, ac, 40, 0]) + payload
            pkt = bytearray(T.XbeeTransport.packet(frame))
        else:
            pkt = bytearray(T.PprzTransport.packet(payload))
        r = rng.random()
        if r < 0.03:
            pkt[rng.randrange(1, len(pkt))] ^= 0x55  # corrupt
        elif r < 0.06:
            out += bytes(rng.randrange(256) for _ in range(rng.randrange(1, 8)))  # junk
        out += pkt
    return bytes(out)


class Sniffer:
    def __init__(self, bus):
        self.msgs = []
        self.lock = threading.Lock()
        IvyInit("diffsniffer", "READY", 0, lambda *a: None, lambda *a: None)
        IvyStart(bus)
        IvyBindMsg(self.on, "^(.*)$")

    def on(self, agent, text):
        with self.lock:
            self.msgs.append(text)


def run_link(kind, bus, _unused, data, transport, uplink_msgs, sniffer):
    """The link opens the slave side of a pseudo-terminal as its modem;
    the test writes downlink bytes to and reads uplink bytes from the master."""
    import tty
    with sniffer.lock:
        sniffer.msgs.clear()
    master, slave = os.openpty()
    tty.setraw(master)
    tty.setraw(slave)
    dev = os.ttyname(slave)
    args = LINKS[kind] + ["-d", dev, "-b", bus, "-transport", transport, "-ac_timeout", "100000"]
    p = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    up = bytearray()
    stop = [False]

    def reader():
        import select as sel
        while not stop[0]:
            r, _, _ = sel.select([master], [], [], 0.1)
            if r:
                try:
                    up.extend(os.read(master, 65536))
                except OSError:
                    break
    th = threading.Thread(target=reader, daemon=True)
    th.start()
    time.sleep(2.5)  # Ivy connection
    for i in range(0, len(data), 997):
        os.write(master, data[i:i + 997])
        time.sleep(0.003)
    time.sleep(1.5 if transport == "pprz" else 4.0)  # XBee: 4 s AT configuration first
    for m in uplink_msgs:
        IvySendMsg(m)
        time.sleep(0.02)
    time.sleep(1.5)
    p.terminate()
    try:
        p.wait(timeout=3)
    except subprocess.TimeoutExpired:
        p.kill()
    stop[0] = True
    th.join()
    err = p.stderr.read().decode("latin-1")
    os.close(master)
    os.close(slave)
    with sniffer.lock:
        msgs = list(sniffer.msgs)
    return msgs, bytes(up), err


def expected(data, transport, tm, dl, uplink):
    """What the link must publish/write, from the verified message layer."""
    msgs, counters = [], {}
    tr = T.XbeeTransport() if transport == "xbee" else T.PprzTransport()

    def use_payload(p, raw):
        try:
            h, v = tm.values_of_payload(p)
            name = tm.message_of_id(h["message_id"]).name
            msgs.append("%d %s" % (h["sender_id"], tm.string_of_message(tm.message_of_id(h["message_id"]), v)))
            c = counters.setdefault(str(h["sender_id"]), [0, 0, 0])
            c[0] += raw
            c[1] += 1
            c[2] = 0 if transport == "xbee" else tr.nb_err  # copied at each message
        except Exception:
            pass

    if transport == "xbee":
        def use(frame):
            f = T.api_parse_frame(frame)
            if f[0] == "rx":
                use_payload(f[1], len(frame) + 4)
        parser = lambda b: tr.parse(use, b)
    else:
        parser = lambda b: tr.parse(lambda p: use_payload(p, len(p) + 4), b)
    # Serial.input: 2048 bytes buffer, drop one byte when full and stuck
    buf, pos = b"", 0
    while pos < len(data) or buf:
        take = 2048 - len(buf)
        chunk = data[pos:pos + take]
        pos += len(chunk)
        buf += chunk
        progressed = False
        while buf:
            used = parser(buf)
            if used > 0:
                buf = buf[used:]
                progressed = True
            elif len(buf) == 2048:
                buf = buf[1:]
            else:
                break
        if pos >= len(data) and not chunk and not progressed:
            break
    reports = {k: (str(v[0]), str(v[1]), str(v[2])) for k, v in counters.items()}
    up = b""
    for m in uplink:
        try:
            msg, v = dl.values_of_string(m.split(" ", 1)[1])
            vd = dict(v)
            dest = vd["ac_id"][1] if msg.link == "forwarded" else 0xFF
            if dest not in (1, 0xFF) or (msg.link == "forwarded" and str(dest) not in reports):
                continue
            p = dl.payload_of_values(msg.id, 0, dest, v)
            if transport == "xbee":
                # frame ids depend on pings: strip_pings compares the payloads only
                up += T.XbeeTransport.packet(T.api_tx16(0xFFFF if dest == 0xFF else dest, p))
            else:
                up += T.PprzTransport.packet(p)
        except Exception:
            pass
    fake = ["link LINK_REPORT %s -1 0 0 %s %s %s 0 0 0 0" % (k, a, b, c) for k, (a, b, c) in reports.items()]
    return msgs + fake, up, ""


def normalize(msgs):
    """Keep telemetry; LINK_REPORT keeps counters only (rates/time vary)."""
    out = []
    for m in msgs:
        if m.startswith("link LINK_REPORT"):
            continue
        if " DATALINK" in m or m.startswith("diffsniffer") or m == "READY":
            continue
        out.append(m)
    return out


def link_reports(msgs):
    last = {}
    for m in msgs:
        if m.startswith("link LINK_REPORT"):
            f = m.split()
            # ac_id link_id run_time rx_lost_time rx_bytes rx_msgs rx_err rates.. tx_msgs ping
            last[f[2]] = (f[6], f[7], f[8])
    return last


def strip_pings(data, transport, dl):
    """Remove PING frames (periodic, timing dependent) from uplink output."""
    t = T.XbeeTransport() if transport == "xbee" else T.PprzTransport()
    frames = []

    def use(p):
        frames.append(p)
    t.parse(use, data)
    ping = dl.message_of_name("PING").id
    out = []
    for p in frames:
        payload = p[5:] if transport == "xbee" else p
        if len(payload) > 3 and payload[3] == ping:
            continue
        out.append(payload)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--transport", default="pprz")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--count", type=int, default=1500)
    ap.add_argument("--reference", choices=["ocaml", "model"], default="ocaml",
                    help="compare with the OCaml link, or with the expected output computed "
                         "from the message layer (when OCaml Ivy programs cannot run)")
    a = ap.parse_args()
    rng = random.Random(a.seed)
    tm = MessageClass("telemetry")
    dl = MessageClass("datalink")
    data = downlink_stream(rng, tm, a.count, a.transport == "xbee")
    # uplink: one Ivy message per forwarded/broadcasted datalink message
    uplink = []
    for msg in sorted(dl.by_id.values(), key=lambda m: m.id):
        if msg.link not in ("forwarded", "broadcasted"):
            continue
        vals = []
        for f in msg.fields:
            v = rand_value(rng, f.type)
            if f.name == "ac_id":
                v = ("int", 1)
            vals.append((f.name, v))
        try:
            uplink.append("gcs " + dl.string_of_message(msg, vals))
        except Exception:
            pass
    port = 2300 + a.seed % 500
    bus = "127.255.255.255:%d" % port
    sniffer = Sniffer(bus)
    fifo = None
    res = {}
    kinds = ("ocaml", "python")
    if a.reference == "model":
        kinds = ("python",)
        res["ocaml"] = expected(data, a.transport, tm, dl, uplink)
    for kind in kinds:
        print("running", kind, "link...", flush=True)
        res[kind] = run_link(kind, bus, fifo, data, a.transport, uplink, sniffer)
        print("  captured %d Ivy messages, %d uplink bytes" % (len(res[kind][0]), len(res[kind][1])), flush=True)
        if res[kind][2].strip():
            print("  stderr:", res[kind][2].strip()[:300], flush=True)
    (mo, uo, eo), (mp, up, ep) = res["ocaml"], res["python"]
    no, np_ = normalize(mo), normalize(mp)
    ok = True
    print("downlink: %d bytes, reference published %d telemetry messages, Python %d" % (len(data), len(no), len(np_)))
    if no != np_:
        ok = False
        import difflib
        for l in list(difflib.unified_diff(no, np_, "ocaml", "python", lineterm="", n=0))[:20]:
            print("   ", l)
    ro, rp = link_reports(mo), link_reports(mp)
    print("LINK_REPORT counters (rx_bytes, rx_msgs, rx_err) per aircraft: reference %s / Python %s" % (ro, rp))
    if ro != rp:
        ok = False
    fo, fp = strip_pings(uo, a.transport, dl), strip_pings(up, a.transport, dl)
    print("uplink: %d Ivy messages -> reference wrote %d frames, Python %d frames" % (len(uplink), len(fo), len(fp)))
    if fo != fp:
        ok = False
        for i, (x, y) in enumerate(zip(fo, fp)):
            if x != y:
                print("    first difference at frame %d:\n      %s\n      %s" % (i, x.hex(), y.hex()))
                break
    print("RESULT:", "IDENTICAL" if ok else "DIFFERENT", flush=True)
    os._exit(0 if ok else 1)


if __name__ == "__main__":
    main()
