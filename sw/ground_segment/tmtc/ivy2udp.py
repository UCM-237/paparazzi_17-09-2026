#!/usr/bin/python3
"""Forward the telemetry of one aircraft from the Ivy bus to a remote host
over UDP (PPRZ frames), and publish on Ivy the datalink messages received
on a local UDP port (as "ground_dl").

Python replacement of the OCaml `ivy2udp`, same options:
  ivy2udp.py [-b bus] [-h host] [-id ac_id] [-p remote_port] [-dp listening_port]
"""

import argparse
import os
import signal
import socket
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "lib", "python"))

from pprzground import ivybus  # noqa: E402
from pprzground.messages import MessageClass  # noqa: E402
from pprzground.transport import PprzTransport  # noqa: E402


def main():
    ap = argparse.ArgumentParser(prog="ivy2udp", add_help=False,
                                 description="Forward telemetry messages from the ivy bus to an UDP connection")
    ap.add_argument("-b", default=ivybus.default_ivy_bus(), help="<ivy bus>")
    ap.add_argument("-h", default="85.214.48.162", help="<remote host>")
    ap.add_argument("-id", default="7", help="<aircraft id>")
    ap.add_argument("-p", type=int, default=4242, help="<remote port>")
    ap.add_argument("-dp", type=int, default=4243, help="<listening port>")
    ap.add_argument("-help", "--help", action="help")
    o, extra = ap.parse_known_args()
    for x in extra:
        sys.stderr.write("Warning: Discarding '%s'" % x)

    tm, dl = MessageClass("telemetry"), MessageClass("datalink")
    out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    remote = (o.h, o.p)
    ac_id = int(o.id)

    ivybus.init("Link", "READY", o.b)

    def on_ivy(_agent, text):
        try:
            msg, vs = tm.values_of_string(text)
            # receiver_id unknown, using 0 instead...
            out.sendto(PprzTransport.packet(tm.payload_of_values(msg.id, ac_id, 0, vs)), remote)
        except Exception:
            pass
    ivybus.IvyBindMsg(on_ivy, "^%s (.*)" % o.id)

    inp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    inp.bind(("0.0.0.0", o.dp))
    transport = PprzTransport()

    def use_dl_message(payload):
        header, values = dl.values_of_payload(payload)
        msg = dl.message_of_id(header["message_id"])
        ivybus.message_send(dl, "ground_dl", msg.name, values)

    def receive():
        while True:
            b = inp.recv(256)
            try:
                if transport.parse(use_dl_message, b) != len(b):
                    raise AssertionError("ivy2udp: incomplete frame")
            except Exception as e:
                sys.stderr.write("%r\n" % e)
                sys.stderr.flush()
    threading.Thread(target=receive, daemon=True).start()

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    while not stop.wait(1.0):
        pass
    ivybus.IvyStop()
    os._exit(0)


if __name__ == "__main__":
    main()
