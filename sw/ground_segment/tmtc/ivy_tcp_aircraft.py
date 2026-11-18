#!/usr/bin/python3
"""Aircraft side of an Ivy-over-TCP link: connects to a remote host, sends
there the telemetry of A/C <id> read on Ivy (PPRZ frames) and publishes on
Ivy (as "ground_dl") the datalink frames received from it.

Python replacement of the OCaml `ivy_tcp_aircraft`, same options:
  ivy_tcp_aircraft.py [-b bus] [-h host] [-id ac_id] [-p port]
"""

import os
import socket
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "lib", "python"))

from pprzground import ivybus, bridge  # noqa: E402


def main():
    o = bridge.parse_args("ivy_tcp_aircraft", [
        ("-b", "bus", ivybus.default_ivy_bus(), str, "<ivy bus> Default is %s" % ivybus.default_ivy_bus()),
        ("-h", "host", "10.31.1.98", str, "<remote host> Default is 10.31.1.98"),
        ("-id", "id", "6", str, "<id> Default is 6"),
        ("-p", "port", 4242, int, "<remote port> Default is 4242"),
    ])
    ivybus.init("ivy_tcp", "READY", o["bus"])
    sock = socket.create_connection((o["host"], o["port"]))
    lock = threading.Lock()
    ac_id = int(o["id"])

    def on_ivy(_agent, text):
        try:
            frame = bridge.frame_of_ivy("telemetry", text, ac_id)
            with lock:
                sock.sendall(frame)
        except Exception:
            pass
    ivybus.IvyBindMsg(on_ivy, "^%s (.*)" % o["id"])

    reader = bridge.StreamToIvy("datalink", sender="ground_dl")
    threading.Thread(target=bridge.read_loop, daemon=True,
                     args=(lambda: sock.recv(256), reader.feed, bridge.hangup("hangup"))).start()
    bridge.wait_forever()


if __name__ == "__main__":
    main()
