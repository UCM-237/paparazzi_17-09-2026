"""Ivy bus helpers mirroring PprzLink.Messages.message_send / message_bind
of the OCaml library."""

import os
import sys
import threading

from ivy.std_api import IvyInit, IvyStart, IvyStop, IvyBindMsg, IvySendMsg, IvyUnBindMsg  # noqa: F401
import logging

logging.getLogger("Ivy").setLevel(logging.WARNING)


def default_ivy_bus():
    """Defivybus.default_ivy_bus"""
    return os.environ.get("IVY_BUS", "127.255.255.255:2010")


_send_lock = threading.Lock()


def send(msg):
    with _send_lock:
        IvySendMsg(msg)


def init(agent, ready="READY", bus=None):
    IvyInit(agent, ready, 0, lambda *a: None, lambda *a: None)
    IvyStart(bus or default_ivy_bus())


def message_send(cls, sender, msg_name, values, timestamp=None, link_id=None):
    msg = cls.message_of_name(msg_name)
    s = cls.string_of_message(msg, values)
    ts = "" if timestamp is None else "%f " % timestamp
    m = "%s%s %s" % (ts, sender, s)
    if len(m) > 10000:
        sys.stderr.write("Discarding long ivy message %s (%d bytes)\n" % (msg_name, len(m)))
        sys.stderr.flush()
        return
    if link_id is None:
        send(m)
    else:
        send("redlink TELEMETRY_MESSAGE %s %i %s" % (sender, link_id, m.replace(" ", ";")))


def message_bind(cls, msg_name, cb, sender=None):
    """cb(sender, values); values = [] if the text cannot be parsed."""
    def on_msg(agent, *args):
        if sender is None:
            snd, text = args[0], args[1]
        else:
            snd, text = sender, args[0]
        try:
            values = cls.values_of_string(text)[1]
        except Exception as e:
            sys.stderr.write("%s\n" % e)
            values = []
        cb(snd, values)
    if sender is None:
        return IvyBindMsg(on_msg, "^([^ ]*) +(%s( .*|$))" % msg_name)
    return IvyBindMsg(on_msg, "^%s +(%s( .*|$))" % (sender, msg_name))
