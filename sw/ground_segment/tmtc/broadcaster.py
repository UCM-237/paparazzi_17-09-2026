#!/usr/bin/python3
"""TCP <-> Ivy broadcaster: a TCP server; for each client, the bytes it
sends are published on Ivy as "<to-tag> <base64>" and the Ivy messages
"<from-tag> <base64>" are decoded and written back to it.

Python replacement of the OCaml `broadcaster`, same options (one process
per connection, as Unix.establish_server does):
  broadcaster.py [-b bus] [-f from_tag] [-t to_tag] [-p port]
"""

import base64
import os
import socket
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "lib", "python"))

from pprzground import ivybus, bridge  # noqa: E402


def handler(conn, o):
    ivybus.init("broadcaster", "READY", o["bus"])
    lock = threading.Lock()

    def on_ivy(_agent, data):
        try:
            with lock:
                conn.sendall(base64.b64decode(data, validate=False))
        except Exception as e:
            sys.stderr.write("%r\n" % (e,))
            sys.stderr.flush()
    ivybus.IvyBindMsg(on_ivy, "^%s (.*)" % o["from"])

    def feed(data):
        ivybus.send("%s %s" % (o["to"], base64.b64encode(data).decode("ascii")))
    threading.Thread(target=bridge.read_loop, daemon=True,
                     args=(lambda: conn.recv(256), feed, bridge.hangup("hangup"))).start()
    bridge.wait_forever()


def main():
    o = bridge.parse_args("broadcaster", [
        ("-b", "bus", ivybus.default_ivy_bus(), str, "<ivy bus> Default is %s" % ivybus.default_ivy_bus()),
        ("-f", "from", "DL", str, "<tag> Default is DL"),
        ("-t", "to", "TM", str, "<tag> Default is TM"),
        ("-p", "port", 4242, int, "<remote port> Default is 4242"),
    ])
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", o["port"]))
    srv.listen(15)
    import signal
    signal.signal(signal.SIGCHLD, signal.SIG_IGN)  # no zombies
    while True:
        conn, _ = srv.accept()
        if os.fork() == 0:
            srv.close()
            handler(conn, o)
            os._exit(0)
        conn.close()


if __name__ == "__main__":
    main()
