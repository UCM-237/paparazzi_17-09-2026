#!/bin/bash
# Differential test: Python sd2log.py vs the OCaml sd2log binary (if built).
#   tests/ground/sd2log/run.sh <server .data log> [...]
set -u
cd "$(dirname "$0")/../../.."
export PAPARAZZI_HOME=${PAPARAZZI_HOME:-$PWD} PAPARAZZI_SRC=${PAPARAZZI_SRC:-$PWD}
T=$(mktemp -d)
trap 'rm -rf $T' EXIT
fail=0
for data in "$@"; do
  /usr/bin/python3 tests/ground/sd2log/make_sdlog.py "$data" $T/in.tlm >/dev/null || { fail=1; continue; }
  rm -rf $T/ml $T/py; mkdir $T/ml $T/py
  ./sw/logalizer/sd2log $T/in.tlm $T/ml 2>$T/ml.err
  /usr/bin/python3 sw/logalizer/sd2log.py $T/in.tlm $T/py 2>$T/py.err
  if diff -q $T/ml.err $T/py.err >/dev/null && diff -rq $T/ml $T/py >/dev/null; then
    echo "OK   $data"
  else
    echo "DIFF $data"; diff $T/ml.err $T/py.err | head; diff -rq $T/ml $T/py; fail=1
  fi
done
exit $fail
