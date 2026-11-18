#!/usr/bin/python3
"""Forward telemetry messages from the ivy bus to a serial connection,
a.k.a. a "reverse" link agent:
  ivy telemetry message -> serial out
  serial in -> ivy datalink message (sent as "ground_dl")
Only the PPRZ message format is supported.

Python replacement of the OCaml `ivy2serial`, same options:
  ivy2serial.py [-id ac_id] [-b bus] [-d device] [-baudrate rate]
"""

import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "lib", "python"))

from pprzground import ivybus, bridge  # noqa: E402


def main():
    o = bridge.parse_args("ivy2serial", [
        ("-id", "id", "1", str, "<AC id> Default is 1"),
        ("-b", "bus", ivybus.default_ivy_bus(), str, "<ivy bus> Default is %s" % ivybus.default_ivy_bus()),
        ("-d", "device", "/dev/ttyUSB0", str, "<serial device> Default is /dev/ttyUSB0"),
        ("-baudrate", "baudrate", "57600", str, "<serial baudrate> Default is 57600"),
    ])
    import serial
    port = serial.Serial(o["device"], int(o["baudrate"]), rtscts=False, timeout=None)
    lock = threading.Lock()

    ivybus.init("ivy2serial", "READY", o["bus"])
    ac_id = int(o["id"])

    def on_ivy(_agent, text):
        try:
            frame = bridge.frame_of_ivy("telemetry", text, ac_id)
            with lock:
                port.write(frame)
                port.flush()
        except Exception:
            pass
    ivybus.IvyBindMsg(on_ivy, "^%s (.*)" % o["id"])

    reader = bridge.StreamToIvy("datalink", sender="ground_dl")

    def read():
        return port.read(max(1, port.in_waiting))
    threading.Thread(target=bridge.read_loop, daemon=True,
                     args=(read, reader.feed, bridge.hangup("Serial link disconnected"))).start()
    bridge.wait_forever()


if __name__ == "__main__":
    main()
