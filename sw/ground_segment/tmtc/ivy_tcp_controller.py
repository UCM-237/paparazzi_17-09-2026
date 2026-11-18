#!/usr/bin/python3
"""Ground side of an Ivy-over-TCP link: connects to a remote host, publishes
on Ivy the telemetry frames received from it (sender = A/C id) and sends
there the datalink messages ("ground_dl ...") read on Ivy.

Python replacement of the OCaml `ivy_tcp_controller`, same options:
  ivy_tcp_controller.py [-b bus] [-h host] [-p port]
"""

import os
import socket
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "lib", "python"))

from pprzground import ivybus, bridge  # noqa: E402


def main():
    o = bridge.parse_args("ivy_tcp_controller", [
        ("-b", "bus", ivybus.default_ivy_bus(), str, "<ivy bus> Default is %s" % ivybus.default_ivy_bus()),
        ("-h", "host", "10.31.1.98", str, "<remote host> Default is 10.31.1.98"),
        ("-p", "port", 4243, int, "<remote port> Default is 4243"),
    ])
    ivybus.init("tcp_ivy", "READY", o["bus"])
    sock = socket.create_connection((o["host"], o["port"]))
    lock = threading.Lock()

    reader = bridge.StreamToIvy("telemetry", sender=None)
    threading.Thread(target=bridge.read_loop, daemon=True,
                     args=(lambda: sock.recv(256), reader.feed, bridge.hangup("hangup"))).start()

    def on_ivy(_agent, text):
        try:
            frame = bridge.frame_of_ivy("datalink", text)
            with lock:
                sock.sendall(frame)
        except Exception as e:
            sys.stderr.write("%r\n" % (e,))
            sys.stderr.flush()
    ivybus.IvyBindMsg(on_ivy, "^ground_dl (.*)")
    bridge.wait_forever()


if __name__ == "__main__":
    main()
