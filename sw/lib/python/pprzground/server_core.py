"""Aircraft state model of the Paparazzi server: ports of
server_globals.ml, aircraft_server.ml, parse_messages_v1.ml, wind.ml,
airprox.ml and quaternion.ml (OCaml ground segment)."""

import math
import time

from pprzgen import geo as LL
from pprzgen import xml_light as X
from pprzgen import ext_xml
from pprzgen.ocaml import Failure, NotFound, Hashtbl, truncate, cm
from .messages import string_of_value

pi = LL.pi

# ---------------------------------------------------------------- globals

FIXEDWING_AP_MODES = ["MANUAL", "AUTO1", "AUTO2", "HOME", "NOGPS"]
ROTORCRAFT_AP_MODES = ["KILL", "SAFE", "HOME", "RATE", "ATT", "R_RCC", "A_RCC", "ATT_C", "R_ZH", "A_ZH", "HOVER",
                       "HOV_C", "H_ZH", "NAV", "RC_D", "CF", "FWD", "MODULE", "FLIP", "GUIDED"]
AUTO2 = 2
GAZ_MODES = ["MANUAL", "THROTTLE", "CLIMB", "ALT"]
LAT_MODES = ["MANUAL", "ROLL_RATE", "ROLL", "COURSE"]
GPS_MODES = ["NOFIX", "NA", "2D", "3D", "DGPS", "RTK"]
STATE_FILTER_MODES = ["UNKNOWN", "INIT", "ALIGN", "OK", "GPS_LOST", "IMU_LOST", "COV_ERR", "IR_CONTRAST", "ERROR"]
GPS_3D = 3
HORIZ_MODES = ["WAYPOINT", "ROUTE", "CIRCLE", "ATTITUDE", "MANUAL"]
IF_MODES = ["OFF", "DOWN", "UP"]


class TelemetryError(Exception):
    def __init__(self, ac_name, msg):
        super().__init__(ac_name, msg)
        self.ac_name, self.msg = ac_name, msg


def modes_from_autopilot(ap_xml):
    def sel(x):
        return ext_xml.attrib_or_default(x, "gcs_mode", "").upper() == "TRUE"
    ap = ext_xml.child(ap_xml, "state_machine", select=sel)
    modes = [x for x in X.children(ap) if X.tag(x) == "mode"]
    out = []
    for x in modes:
        try:
            out.append(X.attrib(x, "shortname"))
        except Exception:
            out.append(ext_xml.attrib(x, "name"))
    return out


# ---------------------------------------------------------------- geometry_2d

M_PI = 3.1415926535897932384626433832795
TWO_M_PI = 2.0 * M_PI
M_PI_TWO = M_PI / 2.0


def rad2deg(d):
    return (d * 180.0) / M_PI


def vect_add(u, v):
    return (u[0] + v[0], u[1] + v[1])


def vect_sub(u, v):
    return (u[0] - v[0], u[1] - v[1])


def vect_make(p1, p2):
    return (p2[0] - p1[0], p2[1] - p1[1])


def vect_norm(v):
    return cm.sqrt(v[0] * v[0] + v[1] * v[1])


def vect_mul_scal(v, m):
    return (m * v[0], m * v[1])


def vect_add_mul_scal(lam, a, v):
    return vect_add(a, vect_mul_scal(v, lam))


def barycenter(pts):
    v = (0.0, 0.0)
    for p in pts:
        v = vect_add(v, p)
    return vect_mul_scal(v, 1.0 / float(len(pts)))


def cart2polar(p):
    return (vect_norm(p), math.atan2(p[1], p[0]))


def polar2cart(r, theta):
    return (r * cm.cos(theta), r * cm.sin(theta))


def wind_dir_from_angle_rad(rad):
    w = 3.0 * M_PI_TWO - rad
    while w > TWO_M_PI:
        w -= TWO_M_PI
    return w


def heading_of_to_angle_rad(angle):
    a = 5.0 * M_PI_TWO - angle
    while a >= TWO_M_PI:
        a -= TWO_M_PI
    return a


def norm_angle_rad(a):
    while a < -M_PI:
        a += TWO_M_PI
    while a > M_PI:
        a -= TWO_M_PI
    return a


def norm_course(c):
    _2pi = 2.0 * pi
    while True:
        if c < 0.0:
            c = c + _2pi
        elif c >= _2pi:
            c = c - _2pi
        else:
            return c


# ---------------------------------------------------------------- GPS time

LEAP_SECONDS = 16
LEAP_SECONDS_LIST = [46828800., 78364801., 109900802., 173059203., 252028804., 315187205., 346723206.,
                     393984007., 425520008., 457056009., 504489610., 551750411., 599184012., 820108813.,
                     914803214., 1025136015., 1119744016., 1167264017.]


def get_gps_tow():
    utc = time.gmtime(time.time())
    wday = (utc.tm_wday + 1) % 7  # OCaml tm_wday: Sunday = 0
    return ((wday * 24 + utc.tm_hour) * 60 + utc.tm_min) * 60 + utc.tm_sec + LEAP_SECONDS


def unix_time_of_tow(tow):
    host_tow = get_gps_tow()
    return time.time() + float(tow - host_tow)


# ---------------------------------------------------------------- aircraft

class NavRef:
    """("geo", Geo) | ("utm", Utm) | ("ltp", ecef list)"""


def add_pos_to_nav_ref(nav_ref, xy, z=0.0):
    x, y = xy
    kind, ref = nav_ref
    if kind == "geo":
        g = ref

        def lat_of_xy(lat, last, _x, _y, n, e):
            while n > 0 and abs(lat - last) > e:
                lat, last = cm.asin((_y + cm.sin(g.posn_lat) * cm.cos(lat) * cm.cos(cm.asin(_x / cm.cos(lat))))
                                    / cm.cos(g.posn_lat)), lat
                n -= 1
            return lat

        m_to_rad = 0.0005399568034557235 * 0.00029088820866572159
        lat = lat_of_xy(g.posn_lat + cm.asin(y * m_to_rad), 0.0, x * m_to_rad, y * m_to_rad, 10, 1.e-7)
        return LL.make_geo(lat, g.posn_long + cm.asin(x * m_to_rad / cm.cos(lat)))
    if kind == "utm":
        return LL.of_utm(LL.utm_add(ref, (x, y)))
    ned = [y, x, 0.0]
    g, _ = LL.geo_of_ecef(LL.ecef_of_ned(ref, ned))
    return g


def get_cam_aov(af_xml):
    default = (LL.deg_to_rad(65.0), LL.deg_to_rad(35.0))
    try:
        sec = ext_xml.child(af_xml, "section", select=lambda x: X.attrib(x, "name") == "CAM")

        def fvalue(name, d):
            try:
                return ext_xml.float_attrib(ext_xml.child(sec, "define", select=lambda x: ext_xml.attrib(x, "name") == name), "value")
            except Exception:
                return d
        return (LL.deg_to_rad(fvalue("CAM_HFV", 65.0)), LL.deg_to_rad(fvalue("CAM_VFV", 35.0)))
    except Exception:
        return default


GPS_NB_CHANNELS = 40
MAX_NB_DL_SETTING_VALUES = 256


class SvInfo:
    __slots__ = ("svid", "flags", "qi", "cno", "elev", "azim", "age")

    def __init__(self, svid=0, flags=0, qi=0, cno=0, elev=0, azim=0, age=0):
        self.svid, self.flags, self.qi, self.cno, self.elev, self.azim, self.age = svid, flags, qi, cno, elev, azim, age


class LinkStatus:
    def __init__(self, last_link_status_date=0.0, rx_lost_time=9999, rx_bytes=0, rx_msgs=0,
                 rx_bytes_rate=0.0, tx_msgs=0, ping_time=9999.0):
        self.last_link_status_date = last_link_status_date
        self.rx_lost_time = rx_lost_time
        self.rx_bytes = rx_bytes
        self.rx_msgs = rx_msgs
        self.rx_bytes_rate = rx_bytes_rate
        self.tx_msgs = tx_msgs
        self.ping_time = ping_time


class Aircraft:
    def __init__(self, id_, name, fp, airframe):
        self.vehicle_type = "unknown"
        self.id = id_
        self.name = name
        self.flight_plan = fp
        self.airframe = airframe
        self.pos = LL.Geo(0.0, 0.0)
        self.unix_time = 0.0
        self.itow = 0
        self.roll = self.pitch = 0.0
        self.gspeed = 0.0
        self.airspeed = -1.0
        self.course = self.heading = self.alt = self.climb = self.agl = 0.0
        self.nav_ref = None
        self.d_hmsl = self.ground_alt = 0.0
        self.desired_pos = LL.Geo(0.0, 0.0)
        self.desired_course = self.desired_altitude = self.desired_climb = 0.0
        self.cur_block = self.cur_stage = 0
        self.flight_time = self.stage_time = self.block_time = 0
        self.throttle = self.throttle_accu = self.rpm = self.temp = 0.0
        self.bat = self.amp = self.power = self.charge = self.energy = 0.0
        self.ap_mode = -1
        self.kill_mode = False
        self.gaz_mode = self.lateral_mode = -1
        self.gps_mode = self.gps_Pacc = 0
        self.state_filter_mode = 0
        self.cam_pan, self.cam_tilt, self.cam_target = 0.0, LL.deg_to_rad(90.0), (0.0, 0.0)
        self.camaov = get_cam_aov(airframe)
        self.fbw_rc_status, self.fbw_rc_mode, self.fbw_rc_rate, self.fbw_bat = "???", "???", 0, 0.0
        self.fbw_pprz_mode_msgs = 0
        self.svinfo = [SvInfo() for _ in range(GPS_NB_CHANNELS)]
        self.dl_setting_values = [None] * MAX_NB_DL_SETTING_VALUES
        self.nb_dl_setting_values = 0
        self.horiz_mode = None   # ("circle", geo, r) | ("segment", g1, g2)
        self.horizontal_mode = 0
        self.waypoints = Hashtbl(3, keymodel=lambda k: k)
        self.survey = None
        self.last_msg_date = 0.0
        self.dist_to_wp = 0.0
        self.dl_uplink_lost_time, self.dl_uplink_msgs, self.dl_downlink_msgs, self.dl_downlink_rate = 9999, 0, 0, 0
        self.link_status = Hashtbl(1, keymodel=lambda k: k)
        self.time_since_last_survey_msg = 1729.0
        self.if_mode, self.if_val1, self.if_val2 = 1, 0.0, 0.0
        self.ap_modes = None


def modes_of_aircraft(ac):
    if ac.ap_modes is not None:
        return ac.ap_modes
    if ac.vehicle_type == "fixedwing":
        return FIXEDWING_AP_MODES
    if ac.vehicle_type == "rotorcraft":
        return ROTORCRAFT_AP_MODES
    return ["UKN"]


# ---------------------------------------------------------------- telemetry

def _fvalue(v):
    k = v[0]
    if k in ("float", "int32", "int64", "int"):
        return float(v[1])
    raise Failure("Receive.log_and_parse: float expected, got '%s'" % string_of_value(v))


def _ivalue(v):
    if v[0] in ("int", "int32", "int64"):
        return v[1]
    raise Failure("Receive.log_and_parse: int expected")


def _foi32value(v):
    if v[0] == "int32":
        return float(v[1])
    raise Failure("Receive.log_and_parse: int32 expected")


def check_index(i, t):
    return -1 if i < 0 or i >= len(t) else i


def update_waypoint(ac, wp_id, p, alt):
    new = (alt, p)
    if ac.waypoints.mem(wp_id):
        if ac.waypoints.find(wp_id) != new:
            ac.waypoints.replace(wp_id, new)
    else:
        ac.waypoints.add(wp_id, new)


def get_rc_status(s):
    return {0: "OK", 1: "LOST", 2: "NONE"}.get(s, "")


POS_FRAC = 2.0 ** 8.0
SPEED_FRAC = 2.0 ** 19.0
ANGLE_FRAC = 2.0 ** 12.0


def geo_hmsl_of_ltp(ned, nav_ref, d_hmsl):
    if nav_ref[0] == "ltp":
        g, alt = LL.geo_of_ecef(LL.ecef_of_ned(nav_ref[1], ned))
        return g, alt + d_hmsl
    return LL.make_geo(0.0, 0.0), 0.0


def hmsl_of_ref(nav_ref, d_hmsl):
    if nav_ref[0] == "ltp":
        _, alt = LL.geo_of_ecef(nav_ref[1])
        return alt + d_hmsl
    return 0.0


def _srtm_or(a):
    try:
        return float(LL.srtm_of_wgs84(a.pos))
    except Exception:
        return a.ground_alt


class Parser:
    """parse_messages_v1.log_and_parse (with its global heading hack)."""

    def __init__(self, wind, send_dl):
        self.heading_from_course = False
        self.wind = wind
        self.send_dl = send_dl  # (msg_name, values) -> datalink message

    def log_and_parse(self, ac_name, a, msg, values):
        vd = {}
        for k, v in values:
            vd.setdefault(k, v)

        def value(x):
            x = x.lower()
            if x not in vd:
                raise Failure("Error: field '%s' not found\n" % x)
            return vd[x]

        def fvalue(x):
            f = _fvalue(value(x))
            if math.isinf(f) or math.isnan(f):
                m = "Non normal number: %f in '%s %s %s'" % (f, ac_name, msg.name,
                                                              " ".join(string_of_value(v) for _, v in values))
                raise TelemetryError(ac_name, m.replace(" ", "_"))
            return f

        ivalue = lambda x: _ivalue(value(x))
        foi32 = lambda x: _foi32value(value(x))
        n = msg.name
        if n != "DOWNLINK_STATUS":
            a.last_msg_date = time.time()
        if n == "GPS":
            a.gps_mode = check_index(ivalue("mode"), GPS_MODES)
            if a.gps_mode >= GPS_3D:
                p = LL.Utm(utm_x=fvalue("utm_east") / 100.0, utm_y=fvalue("utm_north") / 100.0, utm_zone=ivalue("utm_zone"))
                a.pos = LL.of_utm(p)
                self._time(a, fvalue("itow"))
                self._gs_course(ac_name, a, fvalue("speed") / 100.0, fvalue("course") / 10.0)
        elif n == "GPS_LLA":
            a.pos = LL.make_geo_deg(float(ivalue("lat")) / 1e7, float(ivalue("lon")) / 1e7)
            self._time(a, fvalue("itow"))
            a.gspeed = fvalue("speed") / 100.0
            a.course = norm_course(LL.deg_to_rad(fvalue("course") / 10.0))
            if self.heading_from_course:
                a.heading = a.course
            a.agl = a.alt - _srtm_or(a)
            a.gps_mode = check_index(ivalue("mode"), GPS_MODES)
            if a.gspeed > 3.0 and a.ap_mode == AUTO2:
                self.wind.update(ac_name, a.gspeed, a.course)
        elif n == "GPS_SOL":
            a.gps_Pacc = ivalue("Pacc")
        elif n == "GPS_INT":
            self._time(a, fvalue("tow"))
            a.gps_Pacc = ivalue("pacc")
        elif n == "ESTIMATOR":
            a.alt = fvalue("z")
            a.climb = fvalue("z_dot")
            a.agl = a.alt - (_srtm_or(a) if a.gps_mode == GPS_3D else a.ground_alt)
        elif n in ("ROTORCRAFT_FP", "ROTORCRAFT_FP_MIN"):
            if a.nav_ref is not None:
                north, east, up = foi32("north") / POS_FRAC, foi32("east") / POS_FRAC, foi32("up") / POS_FRAC
                a.pos, a.alt = geo_hmsl_of_ltp([north, east, -up], a.nav_ref, a.d_hmsl)
                if n == "ROTORCRAFT_FP":
                    de, dn = foi32("carrot_east") / POS_FRAC, foi32("carrot_north") / POS_FRAC
                    dalt = foi32("carrot_up") / POS_FRAC
                    a.desired_pos = add_pos_to_nav_ref(a.nav_ref, (de, dn), z=dalt)
                    a.desired_altitude = dalt + hmsl_of_ref(a.nav_ref, a.d_hmsl)
                    a.desired_course = foi32("carrot_psi") / ANGLE_FRAC
            if n == "ROTORCRAFT_FP":
                veast, vnorth = foi32("veast") / SPEED_FRAC, foi32("vnorth") / SPEED_FRAC
                a.gspeed = cm.sqrt(vnorth * vnorth + veast * veast)
                a.climb = foi32("vup") / SPEED_FRAC
                a.agl = a.alt - _srtm_or(a)
                a.course = norm_course(math.atan2(veast, vnorth))
                a.heading = norm_course(foi32("psi") / ANGLE_FRAC)
                a.roll = foi32("phi") / ANGLE_FRAC
                a.pitch = foi32("theta") / ANGLE_FRAC
                a.throttle = foi32("thrust") / 9600.0 * 100.0
                a.flight_time = ivalue("flight_time")
            else:
                a.gspeed = fvalue("gspeed") / 100.0
                a.agl = a.alt - _srtm_or(a)
        elif n in ("AIRSPEED", "AIR_DATA"):
            a.airspeed = fvalue("airspeed")
        elif n == "DESIRED":
            if a.nav_ref is not None:
                try:
                    x = fvalue("x")
                except Exception:
                    x = fvalue("desired_x")
                try:
                    y = fvalue("y")
                except Exception:
                    y = fvalue("desired_y")
                a.desired_pos = add_pos_to_nav_ref(a.nav_ref, (x, y))
            try:
                a.desired_altitude = fvalue("altitude")
            except Exception:
                a.desired_altitude = fvalue("desired_altitude")
            try:
                a.desired_climb = fvalue("climb")
            except Exception:
                a.desired_climb = fvalue("desired_climb")
            try:
                a.desired_course = norm_course(fvalue("course"))
            except Exception:
                pass
        elif n in ("NAVIGATION_REF", "NAVIGATION_REF_LLA"):
            if n == "NAVIGATION_REF":
                a.nav_ref = ("utm", LL.Utm(utm_x=fvalue("utm_east"), utm_y=fvalue("utm_north"), utm_zone=ivalue("utm_zone")))
            else:
                a.nav_ref = ("geo", LL.make_geo_deg(float(ivalue("lat")) / 1e7, float(ivalue("lon")) / 1e7))
            a.ground_alt = fvalue("ground_alt")
            a.agl = a.alt - (_srtm_or(a) if a.gps_mode == GPS_3D else a.ground_alt)
        elif n == "INS_REF":
            x, y, z = foi32("ecef_x0") / 100.0, foi32("ecef_y0") / 100.0, foi32("ecef_z0") / 100.0
            alt, hmsl = foi32("alt0") / 1000.0, foi32("hmsl0") / 1000.0
            a.nav_ref = ("ltp", [x, y, z])
            a.d_hmsl = hmsl - alt
            a.ground_alt = hmsl
        elif n == "ATTITUDE":
            roll, pitch = fvalue("phi"), fvalue("theta")
            phi_field = [f for f in msg.fields if f.name == "phi"][0]
            if phi_field.type == ("scalar", "int16"):
                a.roll = roll / 180.0 * pi
                a.pitch = pitch / 180.0 * pi
                self.heading_from_course = True
            else:
                a.roll = roll
                a.pitch = pitch
                a.heading = norm_course(fvalue("psi"))
        elif n == "NAVIGATION":
            a.flight_time = ivalue("flight_time")
            a.block_time = ivalue("block_time")
            a.stage_time = ivalue("stage_time")
            a.cur_block = ivalue("cur_block")
            a.cur_stage = ivalue("cur_stage")
            try:
                a.dist_to_wp = cm.sqrt(fvalue("dist2_wp"))
            except Exception:
                a.dist_to_wp = fvalue("dist_wp")
            a.kill_mode = ivalue("kill_auto_throttle") != 0
        elif n == "ROTORCRAFT_NAV_STATUS":
            a.block_time = ivalue("block_time")
            a.stage_time = ivalue("stage_time")
            a.cur_block = ivalue("cur_block")
            a.cur_stage = ivalue("cur_stage")
            a.horizontal_mode = check_index(ivalue("horizontal_mode"), HORIZ_MODES)
            try:
                a.dist_to_wp = fvalue("dist_wp")
            except Exception:
                a.dist_to_wp = 0.0
        elif n == "ENERGY":
            a.throttle = fvalue("throttle")
            a.bat = fvalue("voltage")
            a.amp = fvalue("current")
            a.power = fvalue("power")
            a.charge = fvalue("charge")
            a.energy = fvalue("energy")
        elif n == "FBW_STATUS":
            a.fbw_bat = fvalue("vsupply")
            a.fbw_pprz_mode_msgs = 0
            a.fbw_rc_rate = ivalue("frame_rate")
            a.fbw_rc_status = {2: "NONE", 1: "LOST"}.get(ivalue("rc_status"), "OK")
            a.fbw_rc_mode = {2: "FAILSAFE", 1: "AUTO"}.get(ivalue("mode"), "MANUAL")
        elif n == "PPRZ_MODE":
            a.vehicle_type = "fixedwing"
            a.gaz_mode = check_index(ivalue("ap_gaz"), GAZ_MODES)
            a.lateral_mode = check_index(ivalue("ap_lateral"), LAT_MODES)
            a.horizontal_mode = check_index(ivalue("ap_horizontal"), HORIZ_MODES)
            a.if_mode = check_index(ivalue("if_calib_mode"), IF_MODES)
            mcu1 = ivalue("mcu1_status")
            if a.fbw_pprz_mode_msgs < 10:
                a.fbw_pprz_mode_msgs += 1
            if a.fbw_pprz_mode_msgs >= 10 and a.fbw_rc_status != "FAILSAFE":
                a.fbw_rc_status = "OK" if mcu1 & 0b1 > 0 else ("NONE" if mcu1 & 0b10 > 0 else "LOST")
                a.fbw_rc_mode = "FAILSAFE" if mcu1 & 0b1000 > 0 else ("AUTO" if mcu1 & 0b100 > 0 else "MANUAL")
            if a.fbw_rc_mode == "FAILSAFE":
                a.ap_mode = -2
            else:
                a.ap_mode = check_index(ivalue("ap_mode"), modes_of_aircraft(a))
        elif n in ("ROTORCRAFT_STATUS", "ROVER_STATUS"):
            a.vehicle_type = "rotorcraft" if n == "ROTORCRAFT_STATUS" else "rover"
            a.fbw_rc_status = get_rc_status(ivalue("rc_status"))
            a.fbw_rc_rate = ivalue("frame_rate")
            a.gps_mode = check_index(ivalue("gps_status"), GPS_MODES)
            a.ap_mode = check_index(ivalue("ap_mode"), modes_of_aircraft(a))
            a.kill_mode = ivalue("ap_motors_on") == 0
            a.bat = fvalue("vsupply")
        elif n == "STATE_FILTER_STATUS":
            a.state_filter_mode = check_index(ivalue("state_filter_mode"), STATE_FILTER_MODES)
        elif n == "DATALINK_REPORT":
            a.dl_uplink_lost_time = ivalue("uplink_lost_time")
            a.dl_uplink_msgs = ivalue("uplink_nb_msgs")
            a.dl_downlink_rate = ivalue("downlink_rate")
            a.dl_downlink_msgs = ivalue("downlink_nb_msgs")
        elif n == "CAM":
            a.cam_pan = LL.deg_to_rad(fvalue("pan"))
            a.cam_tilt = LL.deg_to_rad(fvalue("tilt"))
            a.cam_target = (fvalue("target_x"), fvalue("target_y"))
        elif n == "SVINFO":
            i = ivalue("chn")
            assert i < len(a.svinfo)
            a.svinfo[i] = SvInfo(ivalue("SVID"), ivalue("Flags"), ivalue("QI"), ivalue("CNO"),
                                 ivalue("Elev"), ivalue("Azim"), 0)
        elif n == "CIRCLE":
            if a.nav_ref is not None and a.horizontal_mode == 2:
                a.horiz_mode = ("circle", add_pos_to_nav_ref(a.nav_ref, (fvalue("center_east"), fvalue("center_north"))),
                                truncate(fvalue("radius")))
        elif n == "SEGMENT":
            if a.nav_ref is not None and a.horizontal_mode == 1:
                p1 = add_pos_to_nav_ref(a.nav_ref, (fvalue("segment_east_1"), fvalue("segment_north_1")))
                p2 = add_pos_to_nav_ref(a.nav_ref, (fvalue("segment_east_2"), fvalue("segment_north_2")))
                a.horiz_mode = ("segment", p1, p2)
        elif n == "SETTINGS":
            a.if_val1 = fvalue("slider_1_val")
            a.if_val2 = fvalue("slider_2_val")
        elif n == "SURVEY":
            a.time_since_last_survey_msg = 0.0
            if a.nav_ref is not None:
                p1 = add_pos_to_nav_ref(a.nav_ref, (fvalue("west"), fvalue("south")))
                p2 = add_pos_to_nav_ref(a.nav_ref, (fvalue("east"), fvalue("north")))
                a.survey = (p1, p2)
        elif n == "CALIBRATION":
            a.throttle_accu = fvalue("climb_sum_err")
        elif n == "DL_VALUE":
            i = ivalue("index")
            if i < MAX_NB_DL_SETTING_VALUES:
                a.dl_setting_values[i] = fvalue("value")
                a.nb_dl_setting_values = max(a.nb_dl_setting_values, i + 1)
            else:
                raise Failure("Too much dl_setting values !!!")
        elif n == "WP_MOVED":
            if a.nav_ref is not None and a.nav_ref[0] == "utm":
                try:
                    zone = ivalue("utm_zone")
                except Exception:
                    zone = a.nav_ref[1].utm_zone
                p = LL.Utm(utm_x=fvalue("utm_east"), utm_y=fvalue("utm_north"), utm_zone=zone)
                update_waypoint(a, ivalue("wp_id"), LL.of_utm(p), fvalue("alt"))
        elif n == "WP_MOVED_LLA":
            lat, lon, alt = ivalue("lat"), ivalue("lon"), ivalue("alt")
            update_waypoint(a, ivalue("wp_id"), LL.make_geo_deg(float(lat) / 1e7, float(lon) / 1e7), float(alt) / 1000.0)
        elif n == "WP_MOVED_ENU":
            if a.nav_ref is not None:
                east, north, up = foi32("east") / POS_FRAC, foi32("north") / POS_FRAC, foi32("up") / POS_FRAC
                g, h = geo_hmsl_of_ltp([north, east, -up], a.nav_ref, a.d_hmsl)
                update_waypoint(a, ivalue("wp_id"), g, h)
        elif n == "GENERIC_COM":
            ft = ivalue("flight_time")
            if ft >= a.flight_time:
                a.flight_time = ft
                a.pos = LL.make_geo_deg(fvalue("lat") / 1e7, fvalue("lon") / 1e7)
                a.alt = fvalue("alt")
                a.gspeed = fvalue("gspeed") / 100.0
                a.course = norm_course(fvalue("course") / 1e3)
                if self.heading_from_course:
                    a.heading = a.course
                a.agl = a.alt - _srtm_or(a)
                a.bat = fvalue("vsupply") / 10.0
                a.charge = fvalue("charge") / 10.0
                a.throttle = fvalue("throttle")
                a.ap_mode = check_index(ivalue("ap_mode"), modes_of_aircraft(a))
                a.cur_block = ivalue("nav_block")
        elif n == "MINIMAL_COM":
            a.pos = LL.make_geo_deg(fvalue("lat"), fvalue("lon"))
            a.alt = fvalue("hmsl")
            a.gspeed = fvalue("gspeed")
            a.course = norm_course(fvalue("course"))
            if self.heading_from_course or a.vehicle_type == "fixedwing":
                a.heading = a.course
            a.climb = fvalue("climb")
            a.agl = a.alt - _srtm_or(a)
            a.bat = fvalue("vsupply")
            a.throttle = fvalue("throttle")
            a.ap_mode = check_index(ivalue("ap_mode"), modes_of_aircraft(a))
            a.cur_block = ivalue("nav_block")
            a.gps_mode = check_index(ivalue("gps_mode"), GPS_MODES)
            a.flight_time = ivalue("flight_time")
            if a.gspeed > 3.0 and a.ap_mode == AUTO2:
                self.wind.update(ac_name, a.gspeed, a.course)
        elif n == "FORMATION_SLOT_TM":
            self.send_dl("FORMATION_SLOT", values)
        elif n == "FORMATION_STATUS_TM":
            self.send_dl("FORMATION_STATUS", values)
        elif n == "TCAS_RA":
            self.send_dl("TCAS_RESOLVE", [("ac_id", ("int", ivalue("ac_id"))),
                                          ("ac_id_conflict", ("int", int(a.id))),
                                          ("resolve", ("int", ivalue("resolve")))])

    def _time(self, a, itow):
        a.unix_time = unix_time_of_tow(truncate(itow / 1000.0))
        a.itow = int(itow) if not (math.isnan(itow) or math.isinf(itow)) else -(1 << 63)

    def _gs_course(self, ac_name, a, gspeed, course_deg):
        a.gspeed = gspeed
        a.course = norm_course(LL.deg_to_rad(course_deg))
        if self.heading_from_course:
            a.heading = a.course
        a.agl = a.alt - _srtm_or(a)
        if a.gspeed > 3.0 and a.ap_mode == AUTO2:
            self.wind.update(ac_name, a.gspeed, a.course)


# ---------------------------------------------------------------- wind

class Wind:
    PRECISION = 1e-3

    def __init__(self):
        self.h = {}

    def new_ac(self, id_, n):
        self.h[id_] = {"speeds": [None] * n, "wind_init": (0.0, 0.0)}

    def clear(self, id_):
        w = self.h[id_]
        w["wind_init"] = (0.0, 0.0)

    def update(self, id_, r, course):
        theta = heading_of_to_angle_rad(course)
        speed = polar2cart(r, theta)
        w = self.h[id_]
        i = truncate(float(len(w["speeds"])) * course / 2.0 / pi)
        w["speeds"][i] = speed

    @staticmethod
    def _simplex(p, fmax, step, max_iter, precision):
        f = lambda x: -fmax(x)

        def pv(pt):
            return (pt, f(pt))

        def tsort(t):
            l = sorted(t, key=lambda v: v[1], reverse=True)  # Array.sort (stable merge) on f desc
            return l

        def calcnew(pt, b, alpha):
            return vect_add_mul_scal(alpha, b, vect_make(b, pt))

        def norme2(v):
            return v[0] * v[0] + v[1] * v[1]

        a = pv(p)
        b = pv(vect_add(p, (step, 0.0)))
        c = pv(vect_add(p, (0.0, step)))
        vs = tsort([a, b, c])
        n = 0
        while n < max_iter and norme2(vect_make(vs[0][0], vs[2][0])) > precision:
            A, B, C = vs
            vb = barycenter([A[0], B[0], C[0]])
            vr = calcnew(C[0], vb, -1.0)
            fvr = f(vr)
            if fvr > A[1]:
                ve = calcnew(C[0], vb, -2.0)
                fve = f(ve)
                vs = [(ve, fve), A, B] if fve > fvr else [(vr, fvr), A, B]
            else:
                vc = calcnew(C[0], vb, 0.5)
                fvc = f(vc)
                if fvc > B[1] or fvr > B[1]:
                    v = (vr, fvr) if fvr > fvc else (vc, fvc)
                    if v[1] <= B[1]:
                        vs = [A, B, v]
                    elif v[1] > A[1]:
                        vs = [v, A, B]
                    else:
                        vs = [A, v, B]
                else:
                    vcb = calcnew(B[0], A[0], 0.5)
                    vcc = calcnew(C[0], A[0], 0.5)
                    vs = tsort([A, (vcb, f(vcb)), (vcc, f(vcc))])
            n += 1
        return vs[0]

    def _wind(self, wind_init, speeds, precision):
        def mean(w):
            s = 0.0
            for sp in speeds:
                s = s + vect_norm(vect_sub(sp, w))
            return s / float(len(speeds))

        def cost(w):
            m = mean(w)
            s = 0.0
            for sp in speeds:
                err = vect_norm(vect_sub(sp, w)) - m
                s = s + err * err
            return s / float(len(speeds))

        best = self._simplex(wind_init, cost, 2.0, 100, precision)
        return best[0], mean(best[0]), -best[1]

    def get(self, id_):
        if id_ not in self.h:
            raise Failure("Wind.on_wind_compute: ac %s unknown\n" % id_)
        w = self.h[id_]
        speeds = [s for s in w["speeds"] if s is not None]
        if len(speeds) < 3:
            raise Failure("Wind.on_wind_compute: ac %s not enough data\n" % id_)
        wind, mean, stddev = self._wind(w["wind_init"], speeds, self.PRECISION)
        w["wind_init"] = wind
        r, theta = cart2polar(wind)
        return (wind_dir_from_angle_rad(theta), r, mean, stddev, len(speeds))


# ---------------------------------------------------------------- airprox

def _distance(p1, p2):
    return cm.sqrt(cm.pow(p1[0] - p2[0], 2.0) + cm.pow(p1[1] - p2[1], 2.0))


def check_airprox(ac1, ac2):
    p1, p2 = LL.utm_of(ac1.pos), LL.utm_of(ac2.pos)
    alt_difference = abs(ac1.alt - ac2.alt)
    d0 = _distance((p1.utm_x, p1.utm_y), (p2.utm_x, p2.utm_y))
    if not (alt_difference < 10.0 and d0 < 100.0):
        return None
    halfpi = pi / 2.0
    vx1 = ac1.gspeed * cm.cos(halfpi - ac1.course)
    vx2 = ac2.gspeed * cm.cos(halfpi - ac2.course)
    vy1 = ac1.gspeed * cm.sin(halfpi - ac1.course)
    vy2 = ac2.gspeed * cm.sin(halfpi - ac2.course)
    # (sic) the OCaml code mixes x and y coordinates here
    d1 = _distance((p1.utm_x + vx1 * 0.2, p2.utm_x + vx2 * 0.2), (p1.utm_y + vy1 * 0.2, p2.utm_y + vy2 * 0.2))
    return "CRITICAL" if d1 < d0 else "WARNING"


# ---------------------------------------------------------------- quaternions

def quaternion_from_angle(roll, pitch, yaw):
    cy, sy = cm.cos(yaw * 0.5), cm.sin(yaw * 0.5)
    cr, sr = cm.cos(-roll * 0.5), cm.sin(-roll * 0.5)
    cp, sp = cm.cos(pitch * 0.5), cm.sin(pitch * 0.5)
    return (cy * cr * cp + sy * sr * sp,   # w
            cy * sr * cp - sy * cr * sp,   # x
            cy * cr * sp + sy * sr * cp,   # y
            sy * cr * cp - cy * sr * sp)   # z


def quaternion_to_angle(q):
    w, x, y, z = q
    sinr = 2.0 * (w * x + y * z)
    cosr = 1.0 - 2.0 * (x * x + y * y)
    roll = math.atan2(sinr, cosr)
    sinp = 2.0 * (w * y - z * x)
    absf = sinp if sinp > 0.0 else sinp * -1.0
    pitch = math.copysign(3.1415926535897932384626433832795 / 2.0, sinp) if absf >= 1.0 else cm.asin(sinp)
    siny = +2.0 * (w * z + x * y)
    cosy = +1.0 - 2.0 * (y * y + z * z)
    return (roll, pitch, math.atan2(siny, cosy))


def multiply_quaternion(q1, q2):
    w1, x1, y1, z1 = q1
    w2, x2, y2, z2 = q2
    return ((-x1) * x2 - y1 * y2 - z1 * z2 + w1 * w2,
            (x1) * w2 + y1 * z2 - z1 * y2 + w1 * x2,
            (-x1) * z2 + y1 * w2 + z1 * x2 + w1 * y2,
            (x1) * y2 - y1 * x2 + z1 * w2 + w1 * z2)
