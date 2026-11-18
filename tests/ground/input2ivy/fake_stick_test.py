#!/usr/bin/python3
"""Runs input2ivy.py with a fake stick on a private Ivy bus and checks the
messages against values computed by hand from the OCaml formulas.
  fake_stick_test.py"""
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "..", "..")
sys.path.insert(0, os.path.join(ROOT, "sw", "ground_segment", "joystick"))
sys.path.insert(0, os.path.join(ROOT, "sw", "lib", "python"))
import input2ivy  # noqa: E402
import subprocess  # noqa: E402
from ivy.std_api import IvyStop  # noqa: E402

LISTENER = '''
import sys, time, os
from ivy.std_api import *
IvyInit('listener', 'READY', 0, lambda *a: None, lambda *a: None)
IvyStart(sys.argv[1])
IvyBindMsg(lambda a, m: print(m, flush=True), '^input2ivy (JOYSTICK .*)')
time.sleep(float(sys.argv[2]))
os._exit(0)
'''


class FakeStick:
    def __init__(self):
        self.state = (8, 0, 0, [0] * 6)

    def read(self):
        return self.state

    def check_axis(self):
        return 1


def main():
    bus = "127.255.255.255:2491"
    got = []
    stick = FakeStick()
    stop = threading.Event()
    th = threading.Thread(target=input2ivy.main, args=(["-b", bus, "-ac", "GCS", "xbox_cx10_mission.xml"], stick, stop))
    th.start()
    lis = subprocess.Popen(["/usr/bin/python3", "-c", LISTENER, bus, "2.5"], stdout=subprocess.PIPE, text=True)
    time.sleep(1.5)
    # roll=100 pitch=-50 LT yaw=5 throttle=-127 RT ; buttons A(0) LB(5) start(7)
    stick.state = (8, (1 << 0) | (1 << 5) | (1 << 7), 0, [100, -50, 0, 5, -127, 0])
    time.sleep(0.3)
    stick.state = (8, 0, 0, [100, -50, 0, 5, -127, 0])  # variables keep their values
    time.sleep(0.3)
    stop.set()
    th.join()
    got = [l.strip() for l in lis.communicate()[0].splitlines() if l.strip()]
    # axis1=Fit(yaw: 5 in deadband)=128, axis2=Fit(-throttle)=255, axis3=Fit(roll: 100 -> 73)=201,
    # axis4=Fit(-pitch: -50 -> -20)=148; mode=2 (A), auto=1 (start), button4=LB
    ok = "JOYSTICK 129 128 255 201 148 255 2 1 1" in got and got[-1] == "JOYSTICK 129 128 255 201 148 255 2 1 0" and "JOYSTICK 129 128 128 128 128 255 0 0 0" in got[:3]
    print("\n".join(sorted(set(got))), flush=True)
    print("OK" if ok else "FAIL", flush=True)
    IvyStop()
    os._exit(0 if ok else 1)


if __name__ == "__main__":
    main()
