#!/usr/bin/env python3
"""
Golden-output regression harness for the aircraft code generators.

Runs a generator (the historical OCaml gen_aircraft.out, or its Python
replacement) for every (conf file, aircraft, target) found in conf/, writing
each result to its own directory, then compares two such runs file by file.

  golden.py record --gen sw/tools/generators/gen_aircraft.out --out /tmp/ref
  golden.py record --gen "python3 sw/tools/generators/gen_aircraft.py" --out /tmp/py
  golden.py compare /tmp/ref /tmp/py

Outputs that legitimately depend on time (var/conf/*.conf.gz archives) are
not part of the comparison.
"""

import argparse
import concurrent.futures as cf
import difflib
import os
import shlex
import subprocess
import sys
import re
import xml.etree.ElementTree as ET

# "Version" lines come from `git describe` and change with every commit
_VERSION_RE = re.compile(rb"^(/\* Version .* \*/|# Version .*)$", re.M)
from pathlib import Path

HOME = Path(__file__).resolve().parents[2]
CONF = HOME / "conf"


def find_confs():
    """All conf files declaring at least one <aircraft>."""
    confs = []
    for p in sorted(CONF.rglob("*.xml")):
        if p.is_symlink() or "var" in p.parts:
            continue
        try:
            root = ET.parse(p).getroot()
        except ET.ParseError:
            continue
        if root.tag == "conf" and root.find("aircraft") is not None:
            confs.append(p)
    return confs


def targets_of(airframe_path):
    try:
        root = ET.parse(airframe_path).getroot()
    except (ET.ParseError, FileNotFoundError):
        return []
    names = []
    for t in root.iter("target"):
        n = t.get("name")
        if n and n not in names:
            names.append(n)
    return names


def jobs():
    for conf in find_confs():
        root = ET.parse(conf).getroot()
        for ac in root.findall("aircraft"):
            name, af = ac.get("name"), ac.get("airframe")
            if not name or not af:
                continue
            for tgt in targets_of(CONF / af):
                yield conf, name, tgt


def run_one(gen, outroot, conf, name, tgt):
    key = f"{conf.relative_to(CONF).with_suffix('')}/{name}/{tgt}".replace("/", "__")
    d = Path(outroot) / key
    d.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PAPARAZZI_SRC=str(HOME), PAPARAZZI_HOME=str(HOME), TARGET=tgt, Q="@")
    cmd = shlex.split(gen) + ["-all", "-name", name, "-target", tgt, "-conf", str(conf),
                              "-ac_dir", str(d / "gen"), "-conf_dir", str(d / "ac")]
    r = subprocess.run(cmd, cwd=HOME, env=env, capture_output=True, text=True)
    (d / "exit_code").write_text(f"{r.returncode}\n")
    (d / "stderr").write_text(r.stderr)
    return key, r.returncode


def run_group(gen, outroot, group):
    # Jobs sharing an aircraft name run sequentially: the generator archives
    # the expanded config as var/conf/<timestamp>_<md5>_<name>.conf, which
    # races (gzip of a vanished file) when the same name runs concurrently.
    return [run_one(gen, outroot, *j) for j in group]


def record(args):
    todo = list(jobs())
    if args.only:
        rx = re.compile(args.only)
        todo = [j for j in todo if rx.search(f"{j[0].relative_to(CONF).with_suffix('')}/{j[1]}/{j[2]}".replace("/", "__"))]
    print(f"{len(todo)} (conf, aircraft, target) combinations")
    groups = {}
    for j in todo:
        groups.setdefault(j[1], []).append(j)
    failed = 0
    with cf.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futs = [ex.submit(run_group, args.gen, args.out, g) for g in groups.values()]
        for f in cf.as_completed(futs):
            for key, rc in f.result():
                if rc != 0:
                    failed += 1
    print(f"done, {failed} combinations exited non-zero (recorded, not an error by itself)")


def files_under(d):
    return {p.relative_to(d): p for p in d.rglob("*") if p.is_file()}


def compare(args):
    a, b = Path(args.a), Path(args.b)
    keys = sorted(set(os.listdir(a)) & set(os.listdir(b))) if args.common else sorted(set(os.listdir(a)) | set(os.listdir(b)))
    bad = 0
    for k in keys:
        fa, fb = files_under(a / k), files_under(b / k)
        problems = []
        for rel in sorted(set(fa) | set(fb)):
            if rel.name == "stderr" and not args.stderr:
                continue
            if rel not in fa or rel not in fb:
                problems.append(f"  only in {'A' if rel in fa else 'B'}: {rel}")
                continue
            ta, tb = fa[rel].read_bytes(), fb[rel].read_bytes()
            if not args.with_version:
                ta, tb = _VERSION_RE.sub(b"", ta), _VERSION_RE.sub(b"", tb)
            if ta != tb:
                problems.append(f"  differs: {rel}")
                if args.diff:
                    da = ta.decode(errors="replace").splitlines()
                    db = tb.decode(errors="replace").splitlines()
                    for line in list(difflib.unified_diff(da, db, "A", "B", lineterm="", n=1))[: args.diff]:
                        problems.append("    " + line)
        if problems:
            bad += 1
            print(k)
            print("\n".join(problems))
    print(f"\n{len(keys)} combinations, {bad} with differences")
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("--gen", required=True, help="generator command line")
    r.add_argument("--out", required=True)
    r.add_argument("-j", "--jobs", type=int, default=os.cpu_count())
    r.add_argument("--only", help="regex on the combination key (conf__aircraft__target)")
    c = sub.add_parser("compare")
    c.add_argument("a")
    c.add_argument("b")
    c.add_argument("--diff", type=int, default=0, metavar="N", help="show first N diff lines per file")
    c.add_argument("--stderr", action="store_true", help="also compare stderr")
    c.add_argument("--common", action="store_true", help="only compare combinations present in both")
    c.add_argument("--with-version", action="store_true", help="do not ignore 'Version' lines")
    args = ap.parse_args()
    if args.cmd == "record":
        record(args)
    else:
        sys.exit(compare(args))


if __name__ == "__main__":
    main()
