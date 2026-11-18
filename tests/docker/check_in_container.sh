#!/bin/bash
# Checks the pprz-dev Docker image: build, rover firmwares, NPS simulation.
#   useX=false docker/run.sh paparazziuav/pprz-dev tests/docker/check_in_container.sh [aircraft...]
#   CLEAN_COPY=1: build a copy of the git-tracked sources (like a fresh clone)
#   in /tmp/paparazzi instead of the shared tree, whose build outputs may come
#   from another toolchain (e.g. libopencm3 built with the host's CubeCLT GCC)
cd "$PAPARAZZI_SRC" || exit 1
if [ -n "$CLEAN_COPY" ]; then
  echo "copying the git-tracked sources to /tmp/paparazzi ..."
  mkdir -p /tmp/paparazzi
  git -c safe.directory='*' ls-files -z --recurse-submodules | tar --null -T - -cf - | tar -xf - -C /tmp/paparazzi
  cp conf/conf.xml conf/control_panel.xml /tmp/paparazzi/conf/ 2>/dev/null
  export PAPARAZZI_SRC=/tmp/paparazzi PAPARAZZI_HOME=/tmp/paparazzi
  cd /tmp/paparazzi
fi
fail=0
step() { echo; echo "=== $*"; }
check() { if [ "$1" -eq 0 ]; then echo "OK   $2"; else echo "FAIL $2"; fail=1; fi; }

step "toolchain"
which arm-none-eabi-gcc && arm-none-eabi-gcc --version | head -1
python3 --version
if command -v ocaml ocamlfind >/dev/null 2>&1; then echo "FAIL ocaml is installed"; fail=1; else echo "OK   no ocaml in the image"; fi

if [ -z "$SIM_ONLY" ]; then
step "make"
make > /tmp/make.log 2>&1; check $? "make (log /tmp/make.log)"

acs=${*:-Rover_UCM Rover_Tana Rover_Satur}
for ac in $acs; do
  step "$ac ap"
  make -f Makefile.ac AIRCRAFT=$ac clean_ac > /dev/null 2>&1
  make -f Makefile.ac AIRCRAFT=$ac ap.compile > /tmp/ap_$ac.log 2>&1; r=$?
  check $r "$ac ap.compile $(grep -h -o '[^ ]*ap.elf' /tmp/ap_$ac.log | tail -1)"
  [ $r -ne 0 ] && tail -5 /tmp/ap_$ac.log
done
fi

step "NPS simulation"
make -f Makefile.ac AIRCRAFT=Rover_Tana nps.compile > /tmp/nps.log 2>&1; check $? "Rover_Tana nps.compile"
timeout 250 python3 -u tests/ground/sim_session.py -a Rover_Tana --port 2799 --gcs --log > /tmp/sim.log 2>&1
check $? "simulation session (server, link, NPS, PprzGCS, logs)"
grep -E "FAIL|RESULT" /tmp/sim.log
if grep -q "RESULT: FAIL" /tmp/sim.log; then
  for f in /tmp/simtest_*.log; do echo "--- $f"; grep -v "^\s*$" "$f" | tail -15; done
fi

echo
[ $fail -eq 0 ] && echo "ALL CHECKS PASSED" || echo "SOME CHECKS FAILED"
exit $fail
