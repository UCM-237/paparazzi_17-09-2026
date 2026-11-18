"""Shared pieces of the small Ivy <-> byte-stream relays (ivy2serial,
ivy_tcp_aircraft, ivy_tcp_controller): PPRZ frames built from Ivy text
messages, and PPRZ frames read from a stream published on Ivy.

Unlike the OCaml tools, which parsed each read() on its own (and dropped
frames split across two reads), the stream readers keep the incomplete
tail for the next read."""

import os
import sys

from . import ivybus
from .messages import MessageClass, int_assoc
from .transport import PprzTransport

_classes = {}


def msg_class(name):
    if name not in _classes:
        _classes[name] = MessageClass(name)
    return _classes[name]


def frame_of_ivy(cls_name, text, sender_id=None):
    """PPRZ frame of an Ivy message text "<MSG> <fields...>"; sender_id None
    means the ac_id field of the message (datalink)."""
    cls = msg_class(cls_name)
    msg, vs = cls.values_of_string(text)
    sid = int_assoc("ac_id", vs) if sender_id is None else sender_id
    # receiver_id unknown, using 0 instead...
    return PprzTransport.packet(cls.payload_of_values(msg.id, sid, 0, vs))


class StreamToIvy:
    """Feeds bytes, publishes each complete PPRZ frame on Ivy.
    sender: fixed Ivy sender name, or None for the frame's sender_id."""

    def __init__(self, cls_name, sender=None):
        self.cls = msg_class(cls_name)
        self.sender = sender
        self.transport = PprzTransport()
        self.buf = b""

    def _use(self, payload):
        header, values = self.cls.values_of_payload(payload)
        msg = self.cls.message_of_id(header["message_id"])
        sender = self.sender if self.sender is not None else str(header["sender_id"])
        ivybus.message_send(self.cls, sender, msg.name, values)

    def _safe_use(self, payload):
        try:
            self._use(payload)
        except Exception as e:
            sys.stderr.write("%r\n" % (e,))
            sys.stderr.flush()

    def feed(self, data):
        self.buf += data
        used = self.transport.parse(self._safe_use, self.buf)
        self.buf = self.buf[used:]
        if len(self.buf) > 4096:  # garbage that never completes a frame
            self.buf = self.buf[-512:]


def read_loop(read, feed, on_hangup):
    """Blocking reader thread body: read() returning b'' means hang-up."""
    while True:
        try:
            data = read()
        except OSError as e:
            sys.stderr.write("%s\n" % e)
            data = b""
        if not data:
            on_hangup()
            return
        feed(data)


def hangup(text):
    def f():
        sys.stderr.write(text + "\n")
        sys.stderr.flush()
        try:
            ivybus.IvyStop()
        finally:
            os._exit(1)
    return f


def wait_forever():
    import signal
    import threading
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    while not stop.wait(1.0):
        pass
    ivybus.IvyStop()
    os._exit(0)


def parse_args(prog, options, argv=None):
    """OCaml Arg-like parser: options = [(flag, key, default, conv, help)];
    unknown anonymous arguments print a warning."""
    argv = sys.argv[1:] if argv is None else argv
    vals = {k: d for _, k, d, _, _ in options}
    by_flag = {f: (k, c) for f, k, _, c, _ in options}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-help", "--help"):
            sys.stderr.write("Usage: \n" + "".join("  %s %s\n" % (f, h) for f, _, _, _, h in options))
            sys.exit(0)
        if a in by_flag:
            if i + 1 >= len(argv):
                sys.stderr.write("%s: option '%s' needs an argument.\n" % (prog, a))
                sys.exit(2)
            k, conv = by_flag[a]
            try:
                vals[k] = conv(argv[i + 1])
            except ValueError:
                sys.stderr.write("%s: wrong argument '%s'; option '%s' expects a value.\n" % (prog, argv[i + 1], a))
                sys.exit(2)
            i += 2
        else:
            sys.stderr.write("Warning: Discarding '%s'" % a)
            i += 1
    return vals
