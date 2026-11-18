#!/usr/bin/python3
"""Differential test of the message layer used by link/server:
pprzground.messages vs the OCaml PprzLink library (compiled oracle
tests/ground/pprzlink_oracle.ml, which does not use Ivy).

  D: random telemetry payloads -> Ivy text   (link downlink)
  U: random datalink Ivy texts -> payloads   (link uplink)
"""
import os, random, subprocess, sys, tempfile
HOME = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(HOME, "sw", "lib", "python"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("PAPARAZZI_HOME", HOME)
from pprzground.messages import MessageClass  # noqa
from link_diff import rand_value  # noqa


def build_oracle():
    d = tempfile.mkdtemp()
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pprzlink_oracle.ml")
    subprocess.run(["cp", src, d + "/plk.ml"], check=True)
    env = dict(os.environ, OCAMLPATH=os.path.join(HOME, "var", "lib", "ocaml"))
    subprocess.run("ocamlfind ocamlc -package pprzlink -linkpkg plk.ml -o plk", shell=True, cwd=d, env=env,
                   check=True, stderr=subprocess.DEVNULL)
    return d + "/plk"


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    rng = random.Random(7)
    tm, dl = MessageClass("telemetry"), MessageClass("datalink")
    lines, py = [], []
    tmsgs = sorted(tm.by_id.values(), key=lambda m: m.id)
    for k in range(n):
        msg = tmsgs[k % len(tmsgs)] if k < 3 * len(tmsgs) else rng.choice(tmsgs)
        vals = [(f.name, rand_value(rng, f.type)) for f in msg.fields]
        try:
            p = tm.payload_of_values(msg.id, rng.randrange(256), 0, vals)
        except Exception:
            continue
        if rng.random() < 0.05:   # malformed payloads too
            p = p[:rng.randrange(len(p))] if rng.random() < 0.5 else p + b"\x00"
        lines.append("D " + p.hex())
        try:
            h, v = tm.values_of_payload(p)
            py.append("%d %s" % (h["sender_id"], tm.string_of_message(tm.message_of_id(h["message_id"]), v)))
        except Exception:
            py.append("ERR")
    dmsgs = [m for m in sorted(dl.by_id.values(), key=lambda m: m.id) if m.link]
    for k in range(n // 5):
        msg = dmsgs[k % len(dmsgs)]
        vals = []
        for f in msg.fields:
            v = rand_value(rng, f.type)
            if f.name == "ac_id":
                v = ("int", rng.randrange(1, 255))
            vals.append((f.name, v))
        try:
            text = dl.string_of_message(msg, vals)
        except Exception:
            continue
        lines.append("U " + text)
        try:
            m2, v2 = dl.values_of_string(text)
            ac = dict(v2)["ac_id"][1]
            py.append(dl.payload_of_values(m2.id, 0, ac, v2).hex())
        except Exception:
            py.append("ERR")
    oracle = build_oracle()
    env = dict(os.environ, CAML_LD_LIBRARY_PATH=os.path.join(HOME, "var", "lib", "ocaml", "pprzlink"))
    out = subprocess.run([oracle], input="\n".join(lines) + "\n", capture_output=True, text=True,
                         encoding="latin-1", env=env).stdout.splitlines()
    bad = [(l, o, p) for l, o, p in zip(lines, out, py) if o != p]
    nd = sum(1 for l in lines if l[0] == "D")
    print("%d downlink payloads, %d uplink texts, %d OCaml answers; %d differences"
          % (nd, len(lines) - nd, len(out), len(bad) + abs(len(out) - len(py))))
    for l, o, p in bad[:8]:
        print("  in:     ", l[:150]); print("  ocaml:  ", o[:150]); print("  python: ", p[:150])
    sys.exit(1 if bad or len(out) != len(py) else 0)


if __name__ == "__main__":
    main()
