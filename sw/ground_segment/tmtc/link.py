#!/usr/bin/python3
"""Paparazzi data link: radio modem / UDP <-> Ivy bus.

Python replacement of the OCaml `link` program, same options and behaviour.
Uses the system Python on purpose (the Ivy library is a system package).
"""

import argparse
import os
import random
import select
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "lib", "python"))

from pprzground import ivybus  # noqa: E402
from pprzground.messages import MessageClass, BROADCAST_ID, int_of_value  # noqa: E402
from pprzground import transport as T  # noqa: E402
from pprzgen.ocaml import Hashtbl  # noqa: E402

MY_ID = 0
BUFFER_LEN = 2048
STATUS_PING_DIFF = 500  # ms
OVERSIZE_XBEE = 4


class Status:
    def __init__(self, udp_peername, dead_time):
        self.last_rx_byte = 0
        self.last_rx_msg = 0
        self.rx_byte = 0
        self.rx_msg = 0
        self.rx_err = 0
        self.tx_msg = 0
        self.ms_since_last_msg = dead_time
        self.last_ping = 0.0
        self.last_pong = 0.0
        self.udp_peername = udp_peername


class Link:
    def __init__(self, o):
        self.o = o
        self.lock = threading.RLock()
        self.tm = MessageClass("telemetry")
        self.ground = MessageClass("ground")
        self.dl = MessageClass("datalink")
        self.statuss = Hashtbl(3, keymodel=lambda k: k)
        self.start = time.time()
        self.ts_start = time.time() if o.local_timestamp else None
        self.pprz = T.PprzTransport()
        self.xbee_t = T.XbeeTransport()
        self.last_udp_peername = None
        # XBee
        self.xb_init_done = False
        self.xb_packets = [(b"", -1)] * 256
        self.xb_frame_id = 0

    # ---------------------------------------------------------- Ivy output
    def _ts(self):
        return None if self.ts_start is None else time.time() - self.ts_start

    def send_message_over_ivy(self, sender, name, values):
        ivybus.message_send(self.tm, sender, name, values, timestamp=self._ts(),
                            link_id=self.o.id if self.o.redlink else None)

    def send_ground_over_ivy(self, sender, name, values):
        ivybus.message_send(self.ground, sender, name, values, timestamp=self._ts())

    # ---------------------------------------------------------- status
    def update_status(self, ac_id, buf_size, is_pong, udp_peername=None):
        if self.o.fg:
            print("%.3f %d" % (time.time(), buf_size), flush=True)
        if self.statuss.mem(ac_id):
            st = self.statuss.find(ac_id)
        else:
            st = Status(udp_peername, self.o.ac_timeout)
            self.statuss.add(ac_id, st)
        st.rx_byte += buf_size
        st.rx_msg += 1
        st.rx_err = self.pprz.nb_err  # OCaml: always the PPRZ transport counter
        st.ms_since_last_msg = 0
        if is_pong:
            st.last_pong = time.time()

    def live_aircraft(self, ac_id):
        if not self.statuss.mem(ac_id):
            return False
        return self.o.ac_timeout <= 0 or self.statuss.find(ac_id).ms_since_last_msg < self.o.ac_timeout

    def send_status_msg(self):
        dt = float(self.o.status_period) / 1000.0
        for ac_id, st in self.statuss.items():
            t = int(time.time() - self.start)
            byte_rate = float(st.rx_byte - st.last_rx_byte) / dt
            msg_rate = float(st.rx_msg - st.last_rx_msg) / dt
            st.last_rx_msg = st.rx_msg
            st.last_rx_byte = st.rx_byte
            vs = [("ac_id", ("string", str(ac_id))), ("link_id", ("string", str(self.o.id))),
                  ("run_time", ("int64", t)), ("rx_lost_time", ("int64", st.ms_since_last_msg // 1000)),
                  ("rx_bytes", ("int64", st.rx_byte)), ("rx_msgs", ("int64", st.rx_msg)),
                  ("rx_err", ("int64", st.rx_err)), ("rx_bytes_rate", ("float", byte_rate)),
                  ("rx_msgs_rate", ("float", msg_rate)), ("tx_msgs", ("int64", st.tx_msg)),
                  ("ping_time", ("float", 1000.0 * (st.last_pong - st.last_ping)))]
            self.send_ground_over_ivy("link", "LINK_REPORT", vs)

    def update_ms_since_last_msg(self):
        for _, st in self.statuss.items():
            st.ms_since_last_msg += self.o.status_period // 3

    # ---------------------------------------------------------- downlink
    def use_tele_message(self, payload, raw_data_size=None, udp_peername=None):
        if raw_data_size is None:
            raw_data_size = len(payload)
        try:
            header, values = self.tm.values_of_payload(payload)
            ac_id = header["sender_id"]
            msg = self.tm.message_of_id(header["message_id"])
            self.send_message_over_ivy(str(ac_id), msg.name, values)
            self.update_status(ac_id, raw_data_size, msg.name == "PONG", udp_peername)
        except Exception as e:
            sys.stderr.write("%s\n" % e)
            sys.stderr.flush()

    def xb_use_message(self, frame):
        f = T.api_parse_frame(frame, self.o.xbee_868)
        k = f[0]
        if k == "tx_status":
            frame_id, status = f[1], f[2]
            if status == 1:
                packet, nb = self.xb_packets[frame_id]
                if nb < self.o.xbee_retries:
                    self.xb_packets[frame_id] = (packet, nb + 1)
                    threading.Timer((10 + random.randrange(200)) / 1000.0, self.write, (packet,)).start()
        elif k == "rx":
            self.use_tele_message(f[1], raw_data_size=len(frame) + OVERSIZE_XBEE)

    def parser(self, buf):
        if self.o.transport == "xbee":
            return self.xbee_t.parse(self.xb_use_message, buf)
        peer = self.last_udp_peername if self.o.udp else None
        return self.pprz.parse(lambda p: self.use_tele_message(p, len(p) + 4, peer), buf)

    # ---------------------------------------------------------- uplink
    def write(self, data):
        with self.lock:
            if self.o.udp:
                return
            os.write(self.fd, data)

    def udp_send(self, payload, peer):
        buf = T.PprzTransport.packet(payload)
        addr = self.o.udp_broadcast_addr if self.o.udp_broadcast else peer[0]
        n = self.sock.sendto(buf, (addr, self.o.udp_uplink_port))
        assert n == len(buf)

    def xb_send(self, payload, ac_id=None):
        if not self.xb_init_done:
            return
        dest = 0xFFFF if ac_id is None else ac_id
        self.xb_frame_id = 1 if self.xb_frame_id + 1 >= 256 else self.xb_frame_id + 1
        fid = self.xb_frame_id
        if self.o.xbee_868:
            frame = T.api_tx64(dest, payload, fid, True)
        else:
            frame = T.api_tx16(dest, payload, fid)
        packet = T.XbeeTransport.packet(frame)
        self.xb_packets[fid] = (packet, 1)
        self.write(packet)

    def send(self, ac_id, payload):
        with self.lock:
            if not self.live_aircraft(ac_id):
                return
            if self.statuss.mem(ac_id):
                self.statuss.find(ac_id).tx_msg += 1
            peer = self.statuss.find(ac_id).udp_peername
        if peer is not None:
            self.udp_send(payload, peer)
        elif self.o.transport == "pprz":
            self.write(T.PprzTransport.packet(payload))
        else:
            self.xb_send(payload, ac_id)

    def broadcast(self, payload):
        with self.lock:
            for _, st in self.statuss.items():
                st.tx_msg += 1
            if self.o.udp:
                for ac_id, st in self.statuss.items():
                    if self.live_aircraft(ac_id) and st.udp_peername is not None:
                        self.udp_send(payload, st.udp_peername)
                return
        if self.o.transport == "pprz":
            self.write(T.PprzTransport.packet(payload))
        else:
            self.xb_send(payload)

    def message_uplink(self):
        for mid, msg in self.dl.by_id.items():
            if msg.link == "forwarded":
                ivybus.message_bind(self.dl, msg.name, self._forwarder(msg))
            elif msg.link == "broadcasted" and self.o.ac_info:
                ivybus.message_bind(self.dl, msg.name, self._broadcaster(msg))

    def _forwarder(self, msg):
        def cb(_sender, vs):
            try:
                ac_id = int_of_value(dict(vs)["ac_id"])
                p = self.dl.payload_of_values(msg.id, MY_ID, ac_id, vs)
                self.send(ac_id, p)
            except Exception as e:
                sys.stderr.write("%s\n" % e)
        return cb

    def _broadcaster(self, msg):
        def cb(_sender, vs):
            try:
                self.broadcast(self.dl.payload_of_values(msg.id, MY_ID, BROADCAST_ID, vs))
            except Exception as e:
                sys.stderr.write("%s\n" % e)
        return cb

    def send_ping_msg(self):
        ping = self.dl.message_of_name("PING")
        for ac_id, st in list(self.statuss.items()):
            self.send(ac_id, self.dl.payload_of_values(ping.id, MY_ID, ac_id, []))
            st.last_ping = time.time()

    # ---------------------------------------------------------- XBee init
    def xb_init(self):
        def switch_to_api():
            self.write(T.at_set_my(self.o.xbee_addr).encode())
            if self.o.ch is not None:
                self.write(T.at_set_channel(self.o.ch).encode())
            self.write(T.AT_API_ENABLE.encode())
            self.write(T.AT_EXIT.encode())
            self.xb_init_done = True

        def first():
            self.write(T.AT_COMMAND_SEQUENCE.encode())
            threading.Timer(2.0, switch_to_api).start()

        threading.Timer(2.0, first).start()

    # ---------------------------------------------------------- main
    def open(self):
        o = self.o
        if o.udp:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            if o.udp_broadcast:
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self.sock.bind(("0.0.0.0", o.udp_port))
            self.fd = self.sock.fileno()
        elif o.d.startswith("/dev"):
            import serial
            self.ser = serial.Serial(o.d, int(o.s), rtscts=o.hfc, timeout=0)
            self.fd = self.ser.fileno()
        else:
            self.fd = os.open(o.d, os.O_RDWR)

    def periodic(self, period_ms, f, first_delay_ms=None):
        def loop():
            if first_delay_ms is not None:
                time.sleep(first_delay_ms / 1000.0)
            nxt = time.time()
            while True:
                nxt += period_ms / 1000.0
                time.sleep(max(0.0, nxt - time.time()))
                with self.lock:
                    try:
                        f()
                    except Exception as e:
                        sys.stderr.write("%s\n" % e)
        threading.Thread(target=loop, daemon=True).start()

    def run(self):
        o = self.o
        self.open()
        if o.uplink:
            self.message_uplink()
        self.periodic(o.status_period, self.send_status_msg)
        self.periodic(o.status_period // 3, self.update_ms_since_last_msg)
        if o.uplink:
            self.periodic(o.ping_period, self.send_ping_msg, first_delay_ms=STATUS_PING_DIFF)
        if o.transport == "xbee":
            self.xb_init()
        buf = b""
        while True:
            r, _, x = select.select([self.fd], [], [self.fd])
            if x:
                sys.stderr.write("Modem hangup. Exiting\n")
                os._exit(1)
            try:
                if o.udp:
                    data, peer = self.sock.recvfrom(BUFFER_LEN - len(buf))
                    self.last_udp_peername = peer
                else:
                    data = os.read(self.fd, BUFFER_LEN - len(buf))
            except OSError:
                sys.stderr.write("Modem hangup. Exiting\n")
                os._exit(1)
            if not data:
                if o.d.startswith("/dev") or o.udp:
                    sys.stderr.write("End_of_file\n")
                    continue
                sys.stderr.write("Modem hangup. Exiting\n")
                os._exit(1)
            buf += data
            with self.lock:
                while buf:
                    used = self.parser(buf)
                    if used > 0:
                        buf = buf[used:]
                    elif len(buf) == BUFFER_LEN:
                        buf = buf[1:]
                    else:
                        break


def main():
    ap = argparse.ArgumentParser(prog="link", description="Paparazzi link (modem <-> Ivy bus)")
    ap.add_argument("-b", default=ivybus.default_ivy_bus(), help="<ivy bus>")
    ap.add_argument("-d", default="/dev/ttyUSB0", help="<port>")
    ap.add_argument("-fg", action="store_true", help="Enable trafic statistics on standard output")
    ap.add_argument("-noac_info", dest="ac_info", action="store_false", help="Disables AC traffic info (uplink)")
    ap.add_argument("-nouplink", dest="uplink", action="store_false", help="Disables the uplink")
    ap.add_argument("-s", default="9600", help="<baudrate>")
    ap.add_argument("-ch", type=int, default=None, help="<channel>")
    ap.add_argument("-hfc", action="store_true", help="Enable UART hardware flow control (CTS/RTS)")
    ap.add_argument("-local_timestamp", action="store_true", help="Add local timestamp to messages sent over ivy")
    ap.add_argument("-transport", default="pprz", help="pprz or xbee")
    ap.add_argument("-udp", action="store_true", help="Listen a UDP connection on <udp_port>")
    ap.add_argument("-udp_port", type=int, default=4242)
    ap.add_argument("-udp_uplink_port", type=int, default=4243)
    ap.add_argument("-udp_broadcast", action="store_true")
    ap.add_argument("-udp_broadcast_addr", default="127.255.255.255")
    ap.add_argument("-uplink", action="store_true", help="Deprecated (now default)")
    ap.add_argument("-xbee_addr", type=int, default=0x100)
    ap.add_argument("-xbee_retries", type=int, default=10)
    ap.add_argument("-xbee_868", action="store_true")
    ap.add_argument("-redlink", action="store_true")
    ap.add_argument("-id", type=int, default=-1)
    ap.add_argument("-status_period", type=int, default=1000)
    ap.add_argument("-ping_period", type=int, default=5000)
    ap.add_argument("-ac_timeout", type=int, default=5000)
    o, _unknown = ap.parse_known_args()
    if o.transport not in ("pprz", "xbee"):
        sys.stderr.write("Invalid_argument(\"transport_of_string: %s\")\n" % o.transport)
        sys.exit(1)
    ivybus.init("Link", "READY", o.b)
    if o.id != -1 and not o.redlink:
        sys.stderr.write("\nLINK WARNING: The link id was set to %i but the -redlink flag wasn't set. "
                         "To use this link as a redundant link, set the -redlink flag." % o.id)
    try:
        Link(o).run()
    except KeyboardInterrupt:
        os._exit(0)
    except Exception as e:
        sys.stderr.write("%s\n" % e)
        os._exit(1)


if __name__ == "__main__":
    main()
