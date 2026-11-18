"""Ivy bus helpers mirroring PprzLink.Messages.message_send / message_bind
of the OCaml library."""

import os
import sys
import threading

from ivy.std_api import IvyInit, IvyStart, IvyStop, IvyBindMsg, IvySendMsg, IvyUnBindMsg  # noqa: F401
import logging

logging.getLogger("Ivy").setLevel(logging.WARNING)


def _patch_duplicate_connections():
    """ivy-python only avoids connecting twice to the same agent when it
    initiates the connection. When two agents start at the same time (e.g.
    the programs of a Paparazzi Center session), each one connects to the
    other and they end up with two connections; every message was then sent
    on both and received twice. The C Ivy library (used by the former OCaml
    programs) drops such duplicates. Here each peer is identified by its IP
    and the listening port it announces at the start of the handshake, and a
    message is sent on only one connection per peer."""
    from ivy import ivy as I
    if getattr(I.IvyServer, "_pprz_dedup", False):
        return

    orig_process = I.IvyHandler.process_ivymessage

    def process_ivymessage(self, msg, client):
        try:
            mid, num_id, _ = I.decode_msg(msg)
            if mid == I.START_INIT:
                client.pprz_peer = (client.ip, int(num_id))
        except Exception:
            pass
        return orig_process(self, msg, client)

    def send_msg(self, message):
        self._global_lock.acquire()
        count = 0
        try:
            done = set()
            for client in list(self._clients.values()):
                peer = getattr(client, "pprz_peer", None)
                if peer is not None and peer in done:
                    continue
                if client.send_msg(message):
                    count += 1
                    if peer is not None:
                        done.add(peer)
        finally:
            self._global_lock.release()
        return count

    I.IvyHandler.process_ivymessage = process_ivymessage
    I.IvyServer.send_msg = send_msg
    I.IvyServer._pprz_dedup = True


_patch_duplicate_connections()


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
    m = format_message(cls, sender, msg_name, values, timestamp, link_id)
    if m is not None:
        send(m)


def format_message(cls, sender, msg_name, values, timestamp=None, link_id=None):
    """Ivy text of a message (None if too long), raising like the OCaml code."""
    msg = cls.message_of_name(msg_name)
    s = cls.string_of_message(msg, values)
    ts = "" if timestamp is None else "%f " % timestamp
    m = "%s%s %s" % (ts, sender, s)
    if len(m) > 10000:
        sys.stderr.write("Discarding long ivy message %s (%d bytes)\n" % (msg_name, len(m)))
        sys.stderr.flush()
        return None
    if link_id is None:
        return m
    return "redlink TELEMETRY_MESSAGE %s %i %s" % (sender, link_id, m.replace(" ", ";"))


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


def message_answerer(cls, sender, msg_name, cb):
    """PprzLink.message_answerer: answers '<asker> <asker_id> MSG_NAME_REQ ...'
    with '<asker_id> <sender> MSG_NAME ...'."""
    def on_msg(agent, asker, asker_id, text):
        try:
            values = cb(asker, cls.values_of_string(text)[1])
            m = cls.string_of_message(cls.message_of_name(msg_name), values)
            send("%s %s %s" % (asker_id, sender, m))
        except Exception as e:
            sys.stderr.write("PprzLink.answerer %s:%s: %s\n" % (sender, msg_name, e))
            sys.stderr.flush()
    return IvyBindMsg(on_msg, "^([^ ]*) +([^ ]*) +(%s_REQ.*)" % msg_name)


def bind_raw(cb, regexp):
    return IvyBindMsg(lambda agent, *args: cb(*args), regexp)
