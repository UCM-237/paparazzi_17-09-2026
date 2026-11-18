# Ground segment tests

Python-only (run on the current tree):

- `sim_session.py [-a Rover_Tana] [--gcs] [--log]` - NPS simulator + Python link/server
  (+ PprzGCS) on a private Ivy bus; checks what a GCS relies on.
- `bridges_test.py` - ivy_tcp_aircraft, ivy_tcp_controller, ivy2serial, broadcaster.
- `input2ivy/fake_stick_test.py` - joystick agent with a fake input device.
- `start_sim.sh <port> [ac]` / `start_sim.sh stop <port>` - background simulation.

Differential tests against the former OCaml implementation (they need the
OCaml programs, i.e. a build of the `rover` branch, or of this branch before
the commit "Remove the OCaml sources"):

- `codec_diff.py` + `pprzlink_oracle.ml`, `link_diff.py` - PPRZ/XBee codec and link.
- `server_numeric/run.sh` - bit-exact server numerics.
- `sd2log/run.sh <file.data>...` - SD log converter.
- `../generators/golden.py record` - generator outputs (`compare` needs only Python).
