"""Ivy bus helpers mirroring PprzLink.Messages.message_send / message_bind
of the OCaml library."""

import os
import sys
import threading

from ivy.std_api import IvyInit, IvyStart, IvyStop, IvyBindMsg, IvySendMsg, IvyUnBindMsg  # noqa: F401
import logging

if os.environ.get("PPRZ_IVY_DEBUG"):  # ivy-python protocol trace on stderr
    logging.basicConfig(format="%(asctime)s %(threadName)s %(message)s")
    logging.getLogger("Ivy").setLevel(logging.DEBUG)
else:
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

    # Subscriptions made while a connection is being opened (e.g. the binds a
    # program makes just after starting Ivy) were sent on that connection
    # before the handshake, and the peer dropped it as a protocol error
    # ("Client ... was not properly initialized"): the server could lose the
    # link at startup. The handshake (name + current subscriptions) is now
    # sent atomically, and new subscriptions only go to connections whose
    # handshake has been sent (the others get them in their handshake).
    orig_handle = I.IvyHandler.handle

    class _SkipHello:
        """socket proxy dropping the handshake that the original handle()
        sends (it was already sent), up to its END_REGEXP message"""
        def __init__(self, sock):
            self._sock, self._end = sock, I.encode_message(I.END_REGEXP, 0)
            if not isinstance(self._end, bytes):
                self._end = self._end.encode()
            self._skipping = True

        def send(self, data, *a):
            if self._skipping:
                d = data if isinstance(data, bytes) else data.encode()
                if d == self._end:
                    self._skipping = False
                return len(d)
            return self._sock.send(data, *a)

        def __getattr__(self, name):
            return getattr(self._sock, name)

    def handle(self):
        srv, sock = self.server, self.request
        ip, port = self.client_address[0], self.client_address[1]
        with srv._global_lock:
            client = srv._get_client(ip, port, sock)
            sock.send(I.encode_message(I.START_INIT, srv.port, srv.agent_name))
            for idx, subscr in srv.get_subscriptions():
                sock.send(I.encode_message(I.ADD_REGEXP, idx, subscr))
            sock.send(I.encode_message(I.END_REGEXP, 0))
            client.pprz_hello_sent = True
        self.request = _SkipHello(sock)
        return orig_handle(self)

    def bind_msg(self, on_msg_fct, regexp):
        with self._global_lock:
            idx = self._add_subscription(regexp, on_msg_fct)
            for client in list(self._clients.values()):
                if getattr(client, "pprz_hello_sent", False):
                    client.send_new_subscription(idx, regexp)
        return idx

    def unbind_msg(self, num_id):
        with self._global_lock:
            regexp = self._remove_subscription(num_id)  # KeyError
            for client in list(self._clients.values()):
                if getattr(client, "pprz_hello_sent", False):
                    client.remove_subscription(num_id)
        return regexp

    I.IvyHandler.process_ivymessage = process_ivymessage
    I.IvyHandler.handle = handle
    I.IvyServer.send_msg = send_msg
    # The id an agent announces on the bus is built from its name, and the
    # receivers split the announcement on spaces: with a space in the name
    # ("Paparazzi server", which PprzGCS looks for) an agent did not recognise
    # its own announcement and connected to itself, and all the agents named
    # "Paparazzi ..." had the same id. The id (not the name) has no spaces now.
    orig_init = I.IvyServer.__init__

    def server_init(self, *a, **kw):
        orig_init(self, *a, **kw)
        self.agent_id = self.agent_id.replace(" ", "_")

    I.IvyServer.__init__ = server_init
    I.IvyServer.bind_msg = bind_msg
    I.IvyServer.unbind_msg = unbind_msg
    I.IvyServer._pprz_dedup = True


_patch_duplicate_connections()


def default_ivy_bus():
    """Defivybus.default_ivy_bus"""
    return os.environ.get("IVY_BUS", "127.255.255.255:2010")


_send_lock = threading.Lock()


def send(msg):
    with _send_lock:
        IvySendMsg(msg)


_bus = [None]


def init(agent, ready="READY", bus=None, start=True):
    """start=False: subscribe first, then call start(), so that no agent sees
    this one on the bus before it listens to its requests"""
    IvyInit(agent, ready, 0, lambda *a: None, lambda *a: None)
    _bus[0] = bus or default_ivy_bus()
    if start:
        IvyStart(_bus[0])


def start():
    IvyStart(_bus[0])


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
