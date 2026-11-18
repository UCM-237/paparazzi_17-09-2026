#!/usr/bin/python3
"""End-to-end test of the Ivy relays ivy_tcp_aircraft, ivy_tcp_controller,
ivy2serial (on a pty) and broadcaster, on private Ivy buses.
Frames coming from the stream side are split in two writes on purpose.
  tests/ground/bridges_test.py"""
import base64
import os
import pty
import socket
import subprocess
import sys
import time
import tty

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "sw", "lib", "python"))
os.environ.setdefault("PAPARAZZI_HOME", ROOT)
os.environ.setdefault("PAPARAZZI_SRC", ROOT)
from pprzground.messages import MessageClass  # noqa: E402
from pprzground.transport import PprzTransport  # noqa: E402
from ivy.std_api import IvyInit, IvyStart, IvyBindMsg, IvySendMsg, IvyStop  # noqa: E402

TMTC = os.path.join(ROOT, "sw", "ground_segment", "tmtc")
BUS = "127.255.255.255:2493"
tm, dl = MessageClass("telemetry"), MessageClass("datalink")
ALIVE = "ALIVE 1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16"
got = []
results = []


def check(name, cond):
    results.append(cond)
    print("%-4s %s" % ("OK" if cond else "FAIL", name), flush=True)


def wait_for(pred, t=3.0):
    end = time.time() + t
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def frames(data, cls):
    out = []
    PprzTransport().parse(lambda p: out.append(cls.values_of_payload(p)), data)
    return out


def split_send(write, frame):
    write(frame[:5])
    time.sleep(0.2)
    write(frame[5:])


def start(script, *args):
    return subprocess.Popen(["/usr/bin/python3", os.path.join(TMTC, script), "-b", BUS] + list(args))


def recv_some(conn, t=3.0):
    conn.settimeout(t)
    try:
        return conn.recv(4096)
    except socket.timeout:
        return b""


def main():
    IvyInit("bridges_test", "READY", 0, lambda *a: None, lambda *a: None)
    IvyStart(BUS)
    IvyBindMsg(lambda a, m: got.append(m), "^(.*)$")
    procs = []
    try:
        setting = dl.values_of_string("SETTING 3 7 1.5")
        dl_frame = PprzTransport.packet(dl.payload_of_values(setting[0].id, 0, 7, setting[1]))
        alive = tm.values_of_string(ALIVE)
        tm_frame = PprzTransport.packet(tm.payload_of_values(alive[0].id, 7, 0, alive[1]))

        # ---- ivy_tcp_aircraft
        srv = socket.create_server(("127.0.0.1", 6101))
        procs.append(start("ivy_tcp_aircraft.py", "-h", "127.0.0.1", "-p", "6101", "-id", "7"))
        conn, _ = srv.accept()
        time.sleep(1.0)
        IvySendMsg("7 " + ALIVE)
        f = frames(recv_some(conn), tm)
        check("ivy_tcp_aircraft: Ivy telemetry -> TCP frame",
              len(f) == 1 and f[0][0]["sender_id"] == 7 and f[0][1] == alive[1])
        got.clear()
        split_send(conn.sendall, dl_frame)
        check("ivy_tcp_aircraft: split TCP datalink frame -> Ivy",
              wait_for(lambda: "ground_dl SETTING 3 7 1.500000" in got))

        # ---- ivy_tcp_controller
        srv2 = socket.create_server(("127.0.0.1", 6102))
        procs.append(start("ivy_tcp_controller.py", "-h", "127.0.0.1", "-p", "6102"))
        conn2, _ = srv2.accept()
        time.sleep(1.0)
        got.clear()
        split_send(conn2.sendall, tm_frame)
        check("ivy_tcp_controller: split TCP telemetry frame -> Ivy",
              wait_for(lambda: ("7 " + ALIVE) in got))
        IvySendMsg("ground_dl SETTING 3 7 1.5")
        f = frames(recv_some(conn2), dl)
        check("ivy_tcp_controller: Ivy ground_dl -> TCP frame",
              len(f) == 1 and f[0][0]["sender_id"] == 7 and f[0][1] == setting[1])

        # ---- ivy2serial on a pty
        master, slave = pty.openpty()
        tty.setraw(slave)
        procs.append(start("ivy2serial.py", "-d", os.ttyname(slave), "-id", "7", "-baudrate", "57600"))
        time.sleep(1.5)
        IvySendMsg("7 " + ALIVE)
        os.set_blocking(master, False)
        data = b""
        end = time.time() + 3
        while time.time() < end and not frames(data, tm):
            try:
                data += os.read(master, 4096)
            except BlockingIOError:
                time.sleep(0.05)
        f = frames(data, tm)
        check("ivy2serial: Ivy telemetry -> serial frame", len(f) == 1 and f[0][1] == alive[1])
        got.clear()
        split_send(lambda b: os.write(master, b), dl_frame)
        check("ivy2serial: split serial datalink frame -> Ivy",
              wait_for(lambda: "ground_dl SETTING 3 7 1.500000" in got))

        # ---- broadcaster
        procs.append(start("broadcaster.py", "-p", "6103", "-f", "DLX", "-t", "TMX"))
        time.sleep(1.0)
        c = socket.create_connection(("127.0.0.1", 6103))
        time.sleep(1.0)
        got.clear()
        c.sendall(b"hello\x00\x99")
        check("broadcaster: TCP -> Ivy base64",
              wait_for(lambda: ("TMX " + base64.b64encode(b"hello\x00\x99").decode()) in got))
        IvySendMsg("DLX " + base64.b64encode(b"world\x01").decode())
        check("broadcaster: Ivy base64 -> TCP", recv_some(c) == b"world\x01")
        c.close()
    finally:
        for p in procs:
            p.terminate()
        time.sleep(0.5)
        for p in procs:
            p.kill()
        subprocess.run(["pkill", "-f", "broadcaster.py -b " + BUS])
        IvyStop()
    ok = all(results) and len(results) == 8
    print("ALL OK" if ok else "FAILED", flush=True)
    os._exit(0 if ok else 1)


if __name__ == "__main__":
    main()
