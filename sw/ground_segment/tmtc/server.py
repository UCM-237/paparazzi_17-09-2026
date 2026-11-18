#!/usr/bin/python3
"""Paparazzi server: aircraft state, ground messages for the GCS, logs.

Python replacement of the OCaml `server` program (server.ml and friends),
same options, Ivy messages and log files. Uses the system Python on purpose
(the Ivy library is a system package).

Not ported: the -kml option (KML files for Google Earth); it is accepted
and reported as unsupported.
"""

import argparse
import contextlib
import os
import queue
import random
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "lib", "python"))

from pprzgen import xml_light as X, ext_xml, env, aircraft as AC, fp_proc  # noqa: E402
from pprzgen import geo as LL  # noqa: E402
from pprzgen import module as M  # noqa: E402
from pprzgen.ocaml import Failure, NotFound, Hashtbl, truncate, string_of_float, concat  # noqa: E402
from pprzground import ivybus  # noqa: E402
from pprzground.messages import (MessageClass, string_of_values, json_of_message, string_assoc,  # noqa: E402
                                 int_assoc, float_assoc, assoc, int_of_value)
from pprzground import server_core as SC  # noqa: E402

MY_ID = "ground"
DL_ID = "ground_dl"
AIRCRAFT_MSG_PERIOD = 500
WIND_MSG_PERIOD = 5000
AIRCRAFT_ALERTS_PERIOD = 1000
CAM_MAX_ANGLE = LL.deg_to_rad(89.0)


def _int32(x):
    x &= 0xFFFFFFFF
    return x - (1 << 32) if x >= (1 << 31) else x


def get_indexed_value(t, i, text="UNK"):
    return t[i] if i >= 0 else text


class Server:
    def __init__(self, o):
        self.o = o
        # Like the OCaml server (one GLib main loop), everything runs in a single
        # worker thread: Ivy callbacks and timers only post events. This also
        # guarantees Ivy reception threads never block (no cross-process deadlock).
        self.lock = contextlib.nullcontext()
        self.events = queue.Queue()
        self.ground = MessageClass("ground")
        self.tm = MessageClass("telemetry")
        self.alert = MessageClass("alert")
        self.dl = MessageClass("datalink")
        self.logs_path = concat(env.paparazzi_home, "var", "logs")
        self.conf_xml = ext_xml.parse_file(concat(env.paparazzi_home, "conf", "conf.xml"))
        self.aircrafts = Hashtbl(3)
        self.unknown_aircrafts = Hashtbl(5)
        self.intruders = Hashtbl(3)
        self.start_time = time.time()
        self.wind = SC.Wind()
        self.parser = SC.Parser(self.wind, lambda name, vs: self.dl_send(name, vs))
        self.udp_fd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_sockaddr = (o.udp_json_stream_addr, o.udp_json_stream_port)
        self.log = None

    # ------------------------------------------------------------ sending
    def gsend(self, name, vs):
        ivybus.message_send(self.ground, MY_ID, name, vs)

    def dl_send(self, name, vs):
        ivybus.message_send(self.dl, DL_ID, name, vs)

    def asend(self, name, vs):
        ivybus.message_send(self.alert, MY_ID, name, vs)

    # ------------------------------------------------------------ logs
    def expand_aircraft(self, x):
        ac_name = ext_xml.attrib(x, "name")

        def handle(error_type, msg):
            for l in ("A failure occurred while processing aircraft '" + ac_name + "'",
                      " - " + error_type + " : " + msg,
                      " - '" + ac_name + "' will be ignored by the server",
                      " - Please remove it from 'conf.xml' or fix its parameter(s)"):
                sys.stderr.write(l + "\n")
            sys.stderr.flush()
            return X.Element("ignoring_aircraft", [("name", ac_name)], [])

        try:
            ac = AC.parse_aircraft("", x, parse_all=True)
            try:
                sx = ext_xml.parse_file(concat(env.paparazzi_home, "var", "aircrafts", ac_name, "settings.xml"))
                settings_xml = [X.Element("generated_settings", [], X.children(sx))]
            except Exception:
                settings_xml = []
            fp_file = concat(env.paparazzi_home, "conf", ext_xml.attrib(x, "flight_plan"))
            fp_xml = ext_xml.parse_file(fp_file)
            from pprzgen.gen_flight_plan import ocaml_dirname
            fp_xml = fp_proc.process_includes(ocaml_dirname(fp_file), fp_xml)
            ac_xml = [fp_xml if X.tag(e) == "flight_plan" else e for e in ac.xml]
            if ac.xml:
                return X.Element(X.tag(x), X.attribs(x), ac_xml + settings_xml)
            raise Failure("Nothing to parse")
        except Failure as e:
            return handle("Fail with", str(e))
        except M.ModuleNotFound as e:
            return handle("Module not found", str(e))
        except X.DtdProveError as e:
            return handle("Dtd error", str(e))
        except NotFound:
            return handle("Not found", "sorry, something went wrong somewhere")

    def log_xml(self, timeofday, data_file):
        kids = [self.expand_aircraft(x) if X.tag(x) == "aircraft" else x for x in X.children(self.conf_xml)]
        expanded = X.Element(X.tag(self.conf_xml), X.attribs(self.conf_xml), kids)
        from pprzgen import pprzlink as PL
        return X.Element("configuration", [("time_of_day", string_of_float(timeofday)), ("data_file", data_file)],
                         [expanded, PL.messages_xml()])

    def logger(self):
        d = time.localtime(self.start_time)
        basename = "%02d_%02d_%02d__%02d_%02d_%02d" % (d.tm_year % 100, d.tm_mon, d.tm_mday,
                                                      d.tm_hour, d.tm_min, d.tm_sec)
        if not os.path.exists(self.logs_path):
            print("Creating '%s'" % self.logs_path, flush=True)
            os.mkdir(self.logs_path, 0o755)
        if self.o.log_name == "":
            log_name, data_name = basename + ".log", basename + ".data"
        else:
            # (the OCaml server named the data file ".log" too, so the data
            # overwrote the description of the log)
            name = "%s__%s" % (self.o.log_name, basename)
            log_name, data_name = name + ".log", name + ".data"
        with open(concat(self.logs_path, log_name), "w", encoding="latin-1", newline="") as f:
            f.write("<!-- logged with runtime paparazzi_version " + env.get_paparazzi_version() + " -->\n")
            try:
                with open(env.paparazzi_home + "/var/build_version.txt", encoding="latin-1") as bf:
                    build = bf.readline().rstrip("\n") or "UNKNOWN"
            except Exception:
                build = "UNKNOWN"
            f.write("<!-- logged with build paparazzi_version " + build + " -->\n")
            f.write(X.to_string_fmt(self.log_xml(self.start_time, data_name)))
        return open(concat(self.logs_path, data_name), "w", encoding="latin-1", newline="")

    def time_of_timestamp(self, ts):
        return ts if ts is not None else time.time() - self.start_time

    def write_log(self, ac_name, msg_name, values, timestamp=None):
        if self.log is None:
            return
        s = string_of_values(values)
        t = self.time_of_timestamp(timestamp)
        self.log.write("%.3f %s %s %s\n" % (t, ac_name, msg_name, s))
        self.log.flush()

    # ------------------------------------------------------------ telemetry
    def ac_msg(self, tele, ac_name, ac):
        def cb(ts, m):
            with self.lock:
                try:
                    try:
                        timestamp = float(ts)
                    except Exception:
                        timestamp = None
                    msg, values = tele.values_of_string(m)
                    self.write_log(ac_name, msg.name, values, timestamp)
                    if not self.o.udp_json_stream_disable:
                        if self.aircrafts.mem(ac_name):
                            json_name = "%s (%s)" % (self.aircrafts.find(ac_name).name, ac_name)
                        else:
                            json_name = ac_name
                        t = self.time_of_timestamp(timestamp)
                        jm = '{ "%s": %s, "timestamp": %f }' % (json_name, json_of_message(msg, values), t)
                        data = jm.encode("latin-1")
                        try:
                            n = self.udp_fd.sendto(data, self.udp_sockaddr)
                            assert n == len(data)
                        except OSError:
                            pass
                    self.parser.log_and_parse(ac_name, ac, msg, values)
                except SC.TelemetryError as e:
                    self.gsend("TELEMETRY_ERROR", [("ac_id", ("string", e.ac_name)), ("message", ("string", e.msg))])
                    sys.stderr.write(e.msg + "\n")
                except KeyError as e:
                    sys.stderr.write("Unknown message %s from %s: %s\n" % (e, ac_name, m))
                except Exception as e:
                    sys.stderr.write("%s\n" % e)
        return cb

    # ------------------------------------------------------------ periodic sends
    def send_cam_status(self, a):
        if a.gps_mode < SC.GPS_3D or a.nav_ref is None:
            return
        hfv, vfv = a.camaov
        q = SC.quaternion_from_angle
        tr, tl = q(hfv / -2.0, vfv / 2.0, 0.0), q(hfv / 2.0, vfv / 2.0, 0.0)
        br, bl = q(hfv / -2.0, vfv / -2.0, 0.0), q(hfv / 2.0, vfv / -2.0, 0.0)
        gim = q(0.0, -(a.cam_tilt - LL.deg_to_rad(90.0)), -a.cam_pan)
        acr = q(a.roll, a.pitch, -a.heading)
        mq = SC.multiply_quaternion
        rot = [mq(acr, mq(gim, c)) for c in (tr, tl, bl, br)]

        def bind(x):
            return SC.math.copysign(CAM_MAX_ANGLE, x) if abs(x) > CAM_MAX_ANGLE else x

        def ground_point(qq):
            r, p, y = SC.quaternion_to_angle(qq)
            dx = a.agl * SC.cm.tan(bind(r))
            dy = a.agl * SC.cm.tan(bind(p))
            ux = dx * SC.cm.cos(y) - dy * SC.cm.sin(y)
            uy = dx * SC.cm.sin(y) + dy * SC.cm.cos(y)
            return SC.add_pos_to_nav_ref(("geo", a.pos), (ux, uy))

        g = [ground_point(x) for x in rot]
        lats = "".join("%f," % LL.rad_to_deg(x.posn_lat) for x in g)
        longs = "".join("%f," % LL.rad_to_deg(x.posn_long) for x in g)
        t = SC.add_pos_to_nav_ref(a.nav_ref, a.cam_target)
        self.gsend("CAM_STATUS", [("ac_id", ("string", a.id)), ("lats", ("string", lats)), ("longs", ("string", longs)),
                                  ("cam_target_lat", ("float", LL.rad_to_deg(t.posn_lat))),
                                  ("cam_target_long", ("float", LL.rad_to_deg(t.posn_long)))])

    def send_svsinfo(self, a):
        f = {k: "" for k in ("svid", "flags", "qi", "cno", "elev", "azim", "age")}
        for sv in a.svinfo:
            for k in f:
                f[k] += "%d," % getattr(sv, k)
        s = lambda k: ("string", f[k])
        self.gsend("SVSINFO", [("ac_id", ("string", a.id)), ("pacc", ("int", a.gps_Pacc)),
                               ("svid", s("svid")), ("flags", s("flags")), ("qi", s("qi")), ("msg_age", s("age")),
                               ("cno", s("cno")), ("elev", s("elev")), ("azim", s("azim"))])

    def send_telemetry_status(self, a):
        def payload(link_id, ls):
            now = time.time()
            if link_id == "no_id":
                tslm = now - a.last_msg_date
            else:
                t1 = now - a.last_msg_date
                t2 = now - ls.last_link_status_date + float(ls.rx_lost_time)
                tslm = t1 if t1 > t2 else t2
            return [("ac_id", ("string", a.id)), ("link_id", ("string", link_id)),
                    ("time_since_last_msg", ("float", tslm)), ("rx_bytes", ("int64", ls.rx_bytes)),
                    ("rx_msgs", ("int64", ls.rx_msgs)), ("rx_bytes_rate", ("float", ls.rx_bytes_rate)),
                    ("tx_msgs", ("int64", ls.tx_msgs)), ("uplink_lost_time", ("int64", a.dl_uplink_lost_time)),
                    ("uplink_msgs", ("int", a.dl_uplink_msgs)), ("downlink_msgs", ("int", a.dl_downlink_msgs)),
                    ("downlink_rate", ("int", a.dl_downlink_rate)), ("ping_time", ("float", ls.ping_time))]
        if a.link_status.length() == 0:
            self.gsend("TELEMETRY_STATUS", payload("no_id", SC.LinkStatus()))
        else:
            for link_id, ls in a.link_status.items():
                try:
                    self.gsend("TELEMETRY_STATUS", payload(str(link_id), ls))
                except Exception:
                    pass

    def send_aircraft_msg(self, ac):
        with self.lock:
            try:
                a = self.aircrafts.find(ac)
                f = lambda x: ("float", x)
                g = a.pos
                self.gsend("FLIGHT_PARAM", [
                    ("ac_id", ("string", ac)), ("roll", f(SC.rad2deg(a.roll))), ("pitch", f(SC.rad2deg(a.pitch))),
                    ("heading", f(SC.rad2deg(a.heading))), ("lat", f(LL.rad_to_deg(g.posn_lat))),
                    ("long", f(LL.rad_to_deg(g.posn_long))), ("unix_time", f(a.unix_time)), ("itow", ("int64", a.itow)),
                    ("speed", f(a.gspeed)), ("airspeed", f(a.airspeed)), ("course", f(SC.rad2deg(a.course))),
                    ("alt", f(a.alt)), ("agl", f(a.agl)), ("climb", f(a.climb))])
                if self.aircrafts.length() > 1:
                    cm32 = lambda v: ("int32", _int32(truncate(100.0 * v)))
                    cmi = lambda v: ("int", truncate(100.0 * v))
                    course = ("int", truncate(10.0 * SC.rad2deg(a.course)))
                    if a.vehicle_type == "fixedwing":
                        pos = LL.utm_of(a.pos)
                        self.dl_send("ACINFO", [("ac_id", ("string", ac)), ("utm_east", cm32(pos.utm_x)),
                                                ("utm_north", cm32(pos.utm_y)), ("utm_zone", ("int", pos.utm_zone)),
                                                ("course", course), ("alt", cm32(a.alt)), ("speed", cmi(a.gspeed)),
                                                ("climb", cmi(a.climb)), ("itow", ("int64", a.itow))])
                    else:
                        d7 = lambda v: ("int32", _int32(truncate(SC.rad2deg(v * 1e7))))
                        self.dl_send("ACINFO_LLA", [("ac_id", ("string", ac)), ("lat", d7(a.pos.posn_lat)),
                                                    ("lon", d7(a.pos.posn_long)), ("course", course),
                                                    ("alt", cm32(a.alt - a.d_hmsl)), ("speed", cmi(a.gspeed)),
                                                    ("climb", cmi(a.climb)), ("itow", ("int64", a.itow))])
                if a.nav_ref is not None:
                    self.gsend("NAV_STATUS", [
                        ("ac_id", ("string", ac)), ("cur_block", ("int", a.cur_block)), ("cur_stage", ("int", a.cur_stage)),
                        ("stage_time", ("int64", a.stage_time)), ("block_time", ("int64", a.block_time)),
                        ("target_lat", f(LL.rad_to_deg(a.desired_pos.posn_lat))),
                        ("target_long", f(LL.rad_to_deg(a.desired_pos.posn_long))),
                        ("target_alt", f(a.desired_altitude)), ("target_climb", f(a.desired_climb)),
                        ("target_course", f(LL.rad_to_deg(a.desired_course))), ("dist_to_wp", f(a.dist_to_wp))])
                self.gsend("ENGINE_STATUS", [("ac_id", ("string", ac)), ("throttle", f(a.throttle)),
                                             ("throttle_accu", f(a.throttle_accu)), ("rpm", f(a.rpm)),
                                             ("temp", f(a.temp)), ("bat", f(a.bat)), ("amp", f(a.amp)),
                                             ("charge", f(a.charge))])
                ap_mode = get_indexed_value(SC.modes_of_aircraft(a), a.ap_mode, "FAIL" if a.ap_mode == -2 else "UNK")
                self.gsend("AP_STATUS", [
                    ("ac_id", ("string", ac)), ("flight_time", ("int64", a.flight_time)),
                    ("ap_mode", ("string", ap_mode)), ("gaz_mode", ("string", get_indexed_value(SC.GAZ_MODES, a.gaz_mode))),
                    ("lat_mode", ("string", get_indexed_value(SC.LAT_MODES, a.lateral_mode))),
                    ("horiz_mode", ("string", get_indexed_value(SC.HORIZ_MODES, a.horizontal_mode))),
                    ("gps_mode", ("string", get_indexed_value(SC.GPS_MODES, a.gps_mode))),
                    ("state_filter_mode", ("string", get_indexed_value(SC.STATE_FILTER_MODES, a.state_filter_mode))),
                    ("kill_mode", ("string", "ON" if a.kill_mode else "OFF"))])
                self.send_cam_status(a)
                self.gsend("INFLIGH_CALIB", [("ac_id", ("string", a.id)),
                                             ("if_mode", ("string", get_indexed_value(SC.IF_MODES, a.if_mode))),
                                             ("if_value1", f(a.if_val1)), ("if_value2", f(a.if_val2))])
                self.gsend("FLY_BY_WIRE", [("ac_id", ("string", a.id)), ("rc_mode", ("string", a.fbw_rc_mode)),
                                           ("rc_status", ("string", a.fbw_rc_status)), ("rc_rate", ("int", a.fbw_rc_rate))])
                self.send_svsinfo(a)
                if a.horiz_mode is not None:
                    if a.horiz_mode[0] == "circle":
                        _, gg, r = a.horiz_mode
                        self.gsend("CIRCLE_STATUS", [("ac_id", ("string", a.id)),
                                                     ("circle_lat", f(LL.rad_to_deg(gg.posn_lat))),
                                                     ("circle_long", f(LL.rad_to_deg(gg.posn_long))), ("radius", ("int", r))])
                    else:
                        _, g1, g2 = a.horiz_mode
                        self.gsend("SEGMENT_STATUS", [("ac_id", ("string", a.id)),
                                                      ("segment1_lat", f(LL.rad_to_deg(g1.posn_lat))),
                                                      ("segment1_long", f(LL.rad_to_deg(g1.posn_long))),
                                                      ("segment2_lat", f(LL.rad_to_deg(g2.posn_lat))),
                                                      ("segment2_long", f(LL.rad_to_deg(g2.posn_long)))])
                a.time_since_last_survey_msg += float(AIRCRAFT_MSG_PERIOD) / 1000.0
                if a.time_since_last_survey_msg > 5.0:
                    a.survey = None
                if a.survey is None:
                    self.gsend("SURVEY_STATUS", [("ac_id", ("string", a.id))])
                else:
                    g1, g2 = a.survey
                    self.gsend("SURVEY_STATUS", [("ac_id", ("string", a.id)),
                                                 ("south_lat", f(LL.rad_to_deg(g1.posn_lat))),
                                                 ("west_long", f(LL.rad_to_deg(g1.posn_long))),
                                                 ("north_lat", f(LL.rad_to_deg(g2.posn_lat))),
                                                 ("east_long", f(LL.rad_to_deg(g2.posn_long)))])
                if a.nb_dl_setting_values > 0:
                    csv = "".join("?," if v is None else "%f," % v for v in a.dl_setting_values[:a.nb_dl_setting_values])
                    self.gsend("DL_VALUES", [("ac_id", ("string", a.id)), ("values", ("string", csv))])
                for wp_id, (alt, gg) in a.waypoints.items():
                    try:
                        gnd = float(LL.srtm_of_wgs84(gg))
                    except Exception:
                        gnd = a.ground_alt
                    self.gsend("WAYPOINT_MOVED", [("ac_id", ("string", a.id)), ("wp_id", ("int", wp_id)),
                                                  ("long", f(LL.rad_to_deg(gg.posn_long))),
                                                  ("lat", f(LL.rad_to_deg(gg.posn_lat))),
                                                  ("alt", f(alt)), ("ground_alt", f(gnd))])
                self.send_telemetry_status(a)
            except NotFound:
                sys.stderr.write(ac + "\n")
            except Exception as e:
                sys.stderr.write("%s\n" % e)

    def check_alerts(self, a):
        with self.lock:
            try:
                bat = ext_xml.child(a.airframe, "section", select=lambda x: X.attrib(x, "name") == "BAT")

                def fv(name, d):
                    try:
                        return ext_xml.float_attrib(ext_xml.child(bat, "define", select=lambda x: ext_xml.attrib(x, "name") == name), "value")
                    except Exception:
                        return d
                cat, crit, warn = fv("CATASTROPHIC_BAT_LEVEL", 9.0), fv("CRITIC_BAT_LEVEL", 10.0), fv("LOW_BAT_LEVEL", 10.5)
            except Exception as e:
                sys.stderr.write("%s\n" % e)
                return
            level = None
            if a.bat < 1.0:
                level = "INVALID"
            elif a.bat < cat:
                level = "CATASTROPHIC"
            elif a.bat < crit:
                level = "CRITIC"
            elif a.bat < warn:
                level = "WARNING"
            if level:
                self.asend("BAT_LOW", [("ac_id", ("string", a.id)), ("level", ("string", level)), ("value", ("float", a.bat))])

    def send_wind(self, a):
        with self.lock:
            try:
                cap, wp, mean, stddev, _n = self.wind.get(a.id)
                self.gsend("WIND", [("ac_id", ("string", a.id)), ("dir", ("float", LL.rad_to_deg(cap))),
                                    ("wspeed", ("float", wp)), ("mean_aspeed", ("float", mean)),
                                    ("stddev", ("float", stddev))])
            except Exception:
                pass

    def post(self, f, *args):
        self.events.put((f, args))

    def ev(self, f):
        """Wrap an Ivy callback: run it in the worker thread."""
        return lambda *args: self.post(f, *args)

    def periodic(self, period_ms, f):
        def loop():
            nxt = time.time()
            while True:
                nxt += period_ms / 1000.0
                time.sleep(max(0.0, nxt - time.time()))
                self.post(f)
        threading.Thread(target=loop, daemon=True).start()

    def answerer(self, msg_name, cb):
        """PprzLink.message_answerer, executed in the worker thread."""
        def job(asker, asker_id, text):
            try:
                values = cb(asker, self.ground.values_of_string(text)[1])
                m = self.ground.string_of_message(self.ground.message_of_name(msg_name), values)
                ivybus.send("%s %s %s" % (asker_id, MY_ID, m))
            except Exception as e:
                sys.stderr.write("PprzLink.answerer %s:%s: %s\n" % (MY_ID, msg_name, e))
        ivybus.bind_raw(self.ev(job), "^([^ ]*) +([^ ]*) +(%s_REQ.*)" % msg_name)

    # ------------------------------------------------------------ new aircraft
    def replayed(self, ac_id):
        if len(ac_id) > 6 and ac_id[:6] == "replay":
            return (True, ac_id[6:], "/var/replay/",
                    ext_xml.parse_file(concat(env.paparazzi_home, "var/replay/conf/conf.xml")))
        return (False, ac_id, "", self.conf_xml)

    def get_conf(self, real_id, id_, conf_xml):
        try:
            return ext_xml.child(conf_xml, "aircraft", select=lambda x: ext_xml.attrib(x, "ac_id") == id_)
        except NotFound:
            self.unknown_aircrafts.add(real_id, None)
            raise Failure("Error: A/C '%s' not found" % id_)

    def check_md5sum(self, ac_name, alive, conf_dir):
        with open(concat(conf_dir, "aircraft.md5"), encoding="latin-1") as f:
            md5 = f.readline().rstrip("\n")

        def matches(expected):
            if alive[0] != "array":
                raise Failure("Array expected here")
            arr = alive[1]
            assert len(arr) == len(md5) // 2
            for i, v in enumerate(arr):
                assert expected(i) == int_of_value(v)
        try:
            matches(lambda i: int(md5[2 * i:2 * i + 2], 16))
            return
        except Exception:
            pass
        try:
            matches(lambda i: 0)
            sys.stderr.write("MD5 is ZERO, be carefull with configurations\n")
            return
        except Exception:
            pass
        msg = ("WARNING: live md5 signature for %s does not match current configuration, please reload your code "
               "(disable check with -no_md5_check option)" % ac_name)
        if self.o.no_md5_check:
            sys.stderr.write("%s; continuing anyway as requested\n" % msg)
        else:
            raise Failure(msg)

    def new_aircraft(self, get_md5, real_id):
        is_replayed, id_, root_dir, conf_xml = self.replayed(real_id)
        conf = self.get_conf(real_id, id_, conf_xml)
        ac_name = ext_xml.attrib(conf, "name")
        var_dir = concat(env.paparazzi_home, root_dir, "var", "aircrafts", ac_name)
        xml_fp = ext_xml.child(ext_xml.parse_file(concat(var_dir, "flight_plan.xml")), "flight_plan")
        conf_dir = concat(var_dir, "conf")
        airframe_xml = ext_xml.parse_file(concat(conf_dir, ext_xml.attrib(conf, "airframe")))
        if not is_replayed:
            self.check_md5sum(real_id, get_md5(), conf_dir)
        ac = SC.Aircraft(real_id, ac_name, xml_fp, airframe_xml)
        try:
            pac = AC.parse_aircraft("", conf, parse_af=True, parse_ap=True)
            if pac.autopilots is not None and len(pac.autopilots) == 1:
                ac.ap_modes = SC.modes_from_autopilot(pac.autopilots[0][1].xml)
        except BaseException as e:
            if isinstance(e, (SystemExit, KeyboardInterrupt)):
                raise
            ac.ap_modes = None

        def age():
            with self.lock:
                for sv in ac.svinfo:
                    sv.age += 1
        self.periodic(1000, age)
        tele = MessageClass("telemetry", X.parse_file(concat(env.paparazzi_home, root_dir, "var", "messages.xml")))
        return ac, tele

    def register_aircraft(self, name, a):
        self.aircrafts.add(name, a)
        self.periodic(AIRCRAFT_MSG_PERIOD, lambda: self.send_aircraft_msg(name))
        self.periodic(AIRCRAFT_ALERTS_PERIOD, lambda: self.check_alerts(a))
        # airprox checks against the aircraft already known
        for other_name, other in list(self.aircrafts.items()):
            if other_name != name:
                self.periodic(AIRCRAFT_ALERTS_PERIOD, self._airprox(a, other))
        self.periodic(WIND_MSG_PERIOD, lambda: self.send_wind(a))
        self.wind.new_ac(name, 36)
        ivybus.message_bind(self.ground, "WIND_CLEAR", self.ev(lambda _s, vs: self._wind_clear(vs)))

    def _airprox(self, thisac, ac):
        def check():
            with self.lock:
                try:
                    level = SC.check_airprox(thisac, ac)
                    if level is not None:
                        self.asend("AIR_PROX", [("ac_id", ("string", thisac.id + "," + ac.id)), ("level", ("string", level))])
                except Exception as e:
                    sys.stderr.write("check_airprox: %s\n" % e)
        return check

    def _wind_clear(self, vs):
        with self.lock:
            try:
                self.wind.clear(string_assoc("ac_id", vs))
            except Exception as e:
                sys.stderr.write("%s\n" % e)

    def ident_msg(self, timestamp):
        def cb(name, vs):
            with self.lock:
                try:
                    if not self.aircrafts.mem(name) and not self.unknown_aircrafts.mem(name):
                        ac, tele = self.new_aircraft(lambda: assoc("md5sum", vs), name)
                        handler = self.ev(self.ac_msg(tele, name, ac))
                        if timestamp:
                            ivybus.bind_raw(lambda _g, ts, m: handler(ts or "", m),
                                            "^(([0-9]+\\.[0-9]+) )?%s +(.*)" % name)
                        else:
                            ivybus.bind_raw(lambda m: handler("", m), "^%s +(.*)" % name)
                        self.register_aircraft(name, ac)
                        self.gsend("NEW_AIRCRAFT", [("ac_id", ("string", name))])
                except Exception as e:
                    sys.stderr.write("%s\n" % e)
                    sys.stderr.flush()
        return cb

    # ------------------------------------------------------------ intruders
    def update_intruder(self, _sender, vs):
        with self.lock:
            try:
                id_ = string_assoc("id", vs)
                if not self.intruders.mem(id_):
                    self.intruders.add(id_, {"id": id_, "name": string_assoc("name", vs), "pos": LL.Geo(0.0, 0.0),
                                             "unix_time": 0.0, "itow": 0, "course": 0.0, "alt": 0.0,
                                             "gspeed": 0.0, "climb": 0.0})
                i = self.intruders.find(id_)
                lat, lon = int_assoc("lat", vs), int_assoc("lon", vs)
                i["pos"] = LL.make_geo_deg(float(lat) / 1e7, float(lon) / 1e7)
                i["alt"] = float(int_assoc("alt", vs)) / 1000.0
                i["course"] = float_assoc("course", vs)
                i["gspeed"] = float_assoc("speed", vs)
                i["climb"] = float_assoc("climb", vs)
                i["unix_time"] = time.time()
                self.write_log("ground", "INTRUDER", vs)
            except Failure as e:
                sys.stderr.write("Error parsing INTRUDER message: %s\n" % e)

    def periodic_handle_intruders(self):
        with self.lock:
            for id_, i in list(self.intruders.items()):
                if time.time() - i["unix_time"] > 10.0:
                    self.intruders.remove(id_)
            for id_, i in self.intruders.items():
                try:
                    pos = LL.utm_of(i["pos"])
                    ac_id = 200 + (int(id_) % 50)
                    cm32 = lambda v: ("int32", _int32(truncate(100.0 * v)))
                    self.dl_send("ACINFO", [("ac_id", ("int", ac_id)), ("utm_east", cm32(pos.utm_x)),
                                            ("utm_north", cm32(pos.utm_y)),
                                            ("course", ("int", truncate(10.0 * SC.rad2deg(i["course"])))),
                                            ("alt", cm32(i["alt"])), ("speed", ("int", truncate(100.0 * i["gspeed"]))),
                                            ("climb", ("int", truncate(100.0 * i["climb"]))), ("itow", ("int64", i["itow"]))])
                except Exception as e:
                    sys.stderr.write("%s\n" % e)

    # ------------------------------------------------------------ requests
    def send_aircrafts_msg(self, _asker, values):
        assert values == []
        with self.lock:
            names = []
            for k, _ in self.aircrafts.items():
                names.insert(0, k)
            return [("ac_list", ("string", ",".join(names) + ","))]

    def send_config(self, _asker, args):
        real_id = string_assoc("ac_id", args)
        try:
            _r, ac_id, root_dir, conf_xml = self.replayed(real_id)
            conf = ext_xml.child(conf_xml, "aircraft", select=lambda x: ext_xml.attrib(x, "ac_id") == ac_id)
            ac_name = ext_xml.attrib(conf, "name")
            protocol = ("http://%s:%d" % (self.o.hostname, self.o.port)) if self.o.http else ("file://%s" % env.paparazzi_home)
            prefix = lambda s: "%s/%s%s" % (protocol, root_dir, s)
            fp = prefix(concat("var", "aircrafts", ac_name, "flight_plan.xml"))
            af = prefix(concat("conf", ext_xml.attrib(conf, "airframe")))
            rc = prefix(concat("conf", ext_xml.attrib(conf, "radio")))
            settings = prefix(concat("var", "aircrafts", ac_name, "settings.xml"))
            try:
                col = X.attrib(conf, "gui_color")
            except Exception:
                col = "#%02x%02x%02x" % (random.randrange(256), random.randrange(256), random.randrange(256))
            try:
                name = X.attrib(conf, "name")
            except Exception:
                name = ""
            return [("ac_id", ("string", real_id)), ("flight_plan", ("string", fp)), ("airframe", ("string", af)),
                    ("radio", ("string", rc)), ("settings", ("string", settings)),
                    ("default_gui_color", ("string", col)), ("ac_name", ("string", name))]
        except NotFound:
            raise Failure("ground UNKNOWN %s" % real_id)

    # ------------------------------------------------------------ ground -> uplink
    def _bind_uplink(self, name, handler):
        def cb(sender, vs):
            with self.lock:
                try:
                    handler(vs)
                except Exception as e:
                    sys.stderr.write("%s\n" % e)
        ivybus.message_bind(self.ground, name, self.ev(cb))

    def ground_to_uplink(self):
        def move_wp(vs):
            ac_id = string_assoc("ac_id", vs)
            vd = dict(vs)
            deg7 = lambda k: ("int32", _int32(int(float_assoc(k, vs) * 1e7)))
            out = [("wp_id", vd["wp_id"]), ("ac_id", ("string", ac_id)), ("lat", deg7("lat")), ("lon", deg7("long")),
                   ("alt", ("int32", _int32(truncate(1000.0 * float_assoc("alt", vs) + 0.5))))]
            self.dl_send("MOVE_WP", out)
            self.write_log(ac_id, "MOVE_WP", out)

        def emergency(vs):
            ac_id = string_assoc("ac_id", vs)
            out = [("ac_id", ("string", ac_id)), ("cmd", dict(vs)["cmd"])]
            self.dl_send("EMERGENCY_CMD", out)
            self.write_log(ac_id, "EMERGENCY_CMD", out)

        def setting(vs, get=False):
            ac_id = string_assoc("ac_id", vs)
            vd = dict(vs)
            if get:
                out = [("index", vd["index"]), ("ac_id", ("string", ac_id))]
                name = "GET_SETTING"
            else:
                out = [("index", vd["index"]), ("ac_id", ("string", ac_id)), ("value", vd["value"])]
                name = "SETTING"
            self.dl_send(name, out)
            self.write_log(ac_id, name, out)
            if self.aircrafts.mem(ac_id):
                self.aircrafts.find(ac_id).dl_setting_values[int_of_value(vd["index"])] = None

        def jump(vs):
            ac_id = string_assoc("ac_id", vs)
            out = [("block_id", dict(vs)["block_id"]), ("ac_id", ("string", ac_id))]
            self.dl_send("BLOCK", out)
            self.write_log(ac_id, "BLOCK", out)

        def raw(vs):
            ac_id = string_assoc("ac_id", vs)
            m = string_assoc("message", vs).replace(";", " ")
            msg, v2 = self.dl.values_of_string(m)
            self.dl_send(msg.name, v2)
            self.write_log(ac_id, msg.name, v2)

        def info(vs):
            dest = string_assoc("dest", vs)
            parts = [p for p in dest.split(":") if p != ""]
            if len(parts) == 2:
                name, fd = parts[0], int(parts[1])
            elif len(parts) == 1:
                name, fd = parts[0], 0
            else:
                raise Failure("invalid dest")
            try:
                out = [("ac_id", ("int", int(name))), ("fd", ("int", fd)), ("msg", ("string", string_assoc("msg", vs)))]
                self.dl_send("INFO_MSG_UP", out)
                self.write_log(name, "INFO_MSG_UP", out)
            except Exception:
                pass

        def link_report(vs):
            ac_id = string_assoc("ac_id", vs)
            link_id = int(string_assoc("link_id", vs))
            try:
                a = self.aircrafts.find(ac_id)
                ls = SC.LinkStatus(time.time(), int_assoc("rx_lost_time", vs), int_assoc("rx_bytes", vs),
                                   int_assoc("rx_msgs", vs), float_assoc("rx_bytes_rate", vs),
                                   int_assoc("tx_msgs", vs), float_assoc("ping_time", vs))
                a.link_status.replace(link_id, ls)
                self.write_log(ac_id, "LINK_REPORT", vs)
            except Exception:
                pass

        self._bind_uplink("MOVE_WAYPOINT", move_wp)
        self._bind_uplink("DL_EMERGENCY_CMD", emergency)
        self._bind_uplink("DL_SETTING", setting)
        self._bind_uplink("GET_DL_SETTING", lambda vs: setting(vs, get=True))
        self._bind_uplink("JUMP_TO_BLOCK", jump)
        self._bind_uplink("RAW_DATALINK", raw)
        self._bind_uplink("INFO_MSG_GROUND", info)
        self._bind_uplink("LINK_REPORT", link_report)

    # ------------------------------------------------------------ main
    def run(self):
        o = self.o
        if o.kml:
            sys.stderr.write("Warning: -kml is not supported by this server (KML output not ported)\n")
        if o.logging:
            self.log = self.logger()
        ivybus.message_bind(self.tm, "ALIVE", self.ev(self.ident_msg(o.timestamp)))
        if o.replay_old_log:
            ivybus.message_bind(self.tm, "PPRZ_MODE", self.ev(self.ident_msg(o.timestamp)))
        ivybus.message_bind(self.ground, "AIRCRAFT_DIE", self.ev(lambda _s, vs: self._remove(vs)))
        ivybus.message_bind(self.ground, "INTRUDER", self.ev(self.update_intruder))
        self.ground_to_uplink()
        self.periodic(1000, self.periodic_handle_intruders)
        self.answerer("AIRCRAFTS", self.send_aircrafts_msg)
        self.answerer("CONFIG", self.send_config)
        # join the bus only now: a GCS started with the server asks for the
        # aircraft as soon as it sees it (the configuration of the log can take
        # seconds to build)
        ivybus.start()
        while True:
            f, args = self.events.get()
            try:
                f(*args)
            except Exception as e:
                sys.stderr.write("%s\n" % e)
                sys.stderr.flush()

    def _remove(self, vs):
        with self.lock:
            self.aircrafts.remove(string_assoc("ac_id", vs))


def main():
    ap = argparse.ArgumentParser(prog="server", description="Paparazzi server")
    ap.add_argument("-b", default=ivybus.default_ivy_bus(), help="Ivy bus")
    ap.add_argument("-http", action="store_true", help="Send http: URLs (default is file:)")
    ap.add_argument("-hostname", default="localhost")
    ap.add_argument("-port", type=int, default=8889)
    ap.add_argument("-kml", action="store_true", help="(not supported)")
    ap.add_argument("-kml_no_http", action="store_true", help="(not supported)")
    ap.add_argument("-n", dest="logging", action="store_false", help="Disable log")
    ap.add_argument("-timestamp", action="store_true", help="Bind on timestampped messages")
    ap.add_argument("-no_md5_check", action="store_true")
    ap.add_argument("-log_name", default="")
    ap.add_argument("-udp_json_stream_addr", default="127.0.0.1")
    ap.add_argument("-udp_json_stream_port", type=int, default=9870)
    ap.add_argument("-udp_json_stream_disable", action="store_true")
    ap.add_argument("-replay_old_log", action="store_true")
    o, unknown = ap.parse_known_args()
    for x in unknown:
        sys.stderr.write("%s: Warning: Don't do anything with '%s' argument\n" % (sys.argv[0], x))
    ivybus.init("Paparazzi server", "READY", o.b, start=False)
    try:
        Server(o).run()
    except KeyboardInterrupt:
        os._exit(0)


if __name__ == "__main__":
    main()
