"""Ports of the parts of sw/lib/ocaml/{latlong,egm96,srtm,geometry_2d,
ocaml_tools}.ml used by the flight plan generator.

Floating point expressions keep the exact OCaml evaluation order so that
results are bit-identical (both use the C math library).
"""

import bz2
import math
import os
import subprocess
from collections import namedtuple

from .ocaml import concat as _concat
from . import env
from .ocaml import Failure, NotFound, InvalidArgument, truncate, float_of_string, int_of_string, cm

pi = 3.14159265358979323846


def ocaml_div(a, b):
    """OCaml integer division (truncates toward zero)."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


def ocaml_mod(a, b):
    """OCaml `mod` (sign of the dividend)."""
    return a - b * ocaml_div(a, b)


# ---------------------------------------------------------------- units

_PIRADIAN = {"semi": 2.0 ** 31.0, "rad": pi, "deg": 180.0, "grd": 200.0}


def conv(u1, u2, x):
    """(u1 >> u2) x"""
    return (x * _PIRADIAN[u2]) / _PIRADIAN[u1]


def deg_to_rad(x):
    return conv("deg", "rad", x)


def rad_to_deg(x):
    return conv("rad", "deg", x)


Geo = namedtuple("Geo", "posn_lat posn_long")
Utm = namedtuple("Utm", "utm_x utm_y utm_zone")


def norm_angle(long):
    if long >= pi:
        return long - 2.0 * pi
    if long < -pi:
        return long + 2.0 * pi
    return long


def make_geo(lat, long):
    return Geo(posn_lat=lat, posn_long=norm_angle(long))


def valid_geo(g):
    phi, lam = g.posn_lat, g.posn_long
    return phi >= (-pi / 2.0) and phi <= pi / 2.0 and lam >= -pi and lam < pi


def make_geo_deg(lat, long):
    return Geo(posn_long=norm_angle(deg_to_rad(long)), posn_lat=deg_to_rad(lat))


def decimal(d, m, s):
    return float(d) + float(m) / 60.0 + s / 3600.0


Ellipsoid = namedtuple("Ellipsoid", "dx dy dz a df e")
WGS84 = Ellipsoid(0.0, 0.0, 0.0, 6378137.0, 0.0033528106647474805, 0.08181919106)


# ---------------------------------------------------------------- complex

class _C:
    __slots__ = ("re", "im")

    def __init__(self, re, im):
        self.re = re
        self.im = im


def _scal(a, z):
    return _C(z.re * a, z.im * a)


def _add(x, y):
    return _C(x.re + y.re, x.im + y.im)


def _sub(x, y):
    return _C(x.re - y.re, x.im - y.im)


def _exp(x):
    e = cm.exp(x.re)
    return _C(e * cm.cos(x.im), e * cm.sin(x.im))


def _i(z):
    return _C(-z.im, z.re)


def _csin(z):
    iz = _i(z)
    return _i(_scal(-0.5, _sub(_exp(iz), _exp(_scal(-1.0, iz)))))


# ---------------------------------------------------------------- UTM


def latitude_isometrique(phi, e):
    return cm.log(cm.tan(pi / 4.0 + phi / 2.0)) - e / 2.0 * cm.log((1.0 + e * cm.sin(phi)) / (1.0 - e * cm.sin(phi)))


def inverse_latitude_isometrique(lat, e, epsilon):
    exp_l = cm.exp(lat)
    pi_2 = pi / 2.0
    phi = 2.0 * cm.atan(exp_l) - pi_2
    while True:
        sin_phi = e * cm.sin(phi)
        phi2 = 2.0 * cm.atan(cm.pow((1.0 + sin_phi) / (1.0 - sin_phi), e / 2.0) * exp_l) - pi_2
        if abs(phi2 - phi) < epsilon:
            return phi2
        phi = phi2


def _serie5(cc, e):
    ee = [cm.pow(e, float(2 * i)) for i in range(len(cc[0]))]
    res = []
    for cci in cc:
        x = 0.0
        for j in range(len(cci)):
            x = x + cci[j] * ee[j]
        res.append(x)
    return res


_COEFF_MERC = [
    [1.0, -1.0 / 4.0, -3.0 / 64.0, -5.0 / 256.0, -175.0 / 16384.0],
    [0.0, 1.0 / 8.0, -1.0 / 96.0, -9.0 / 1024.0, -901.0 / 184320.0],
    [0.0, 0.0, 13.0 / 768.0, 17.0 / 5120.0, -311.0 / 737280.0],
    [0.0, 0.0, 0.0, 61.0 / 15360.0, 899.0 / 430080.0],
    [0.0, 0.0, 0.0, 0.0, 49561.0 / 41287680.0],
]
_COEFF_MERC_INV = [
    _COEFF_MERC[0],
    [0.0, 1.0 / 8.0, 1.0 / 48.0, 7.0 / 2048.0, 1.0 / 61440.0],
    [0.0, 0.0, 1.0 / 768.0, 3.0 / 1280.0, 559.0 / 368640.0],
    [0.0, 0.0, 0.0, 17.0 / 30720.0, 283.0 / 430080.0],
    [0.0, 0.0, 0.0, 0.0, 4397.0 / 41287680.0],
]

_K0, _XS = 0.9996, 500000.0
_C_UTM = _serie5(_COEFF_MERC, WGS84.e)
_C_UTM_INV = _serie5(_COEFF_MERC_INV, WGS84.e)


def utm_of(geo, zone=None):
    """Latlong.utm_of ?zone WGS84"""
    e = WGS84.e
    n = _K0 * WGS84.a
    c = _C_UTM
    lam, phi = geo.posn_long, geo.posn_lat
    if not valid_geo(geo):
        raise InvalidArgument("Latlong.utm_of")
    lambda_deg = truncate(cm.floor(rad_to_deg(lam)))
    if zone is None:
        z = ocaml_div(lambda_deg + 180, 6) + 1
        lambda_c = deg_to_rad(float(lambda_deg - ocaml_mod(ocaml_mod(lambda_deg, 6) + 6, 6) + 3))
    else:
        z = zone
        lambda_c = deg_to_rad(float((zone - 1) * 6 - 180 + 3))
    ll = latitude_isometrique(phi, e)
    dl = lam - lambda_c
    phi2 = cm.asin(cm.sin(dl) / cm.cosh(ll))
    ll2 = latitude_isometrique(phi2, 0.0)
    lambda2 = cm.atan(cm.sinh(ll) / cm.cos(dl))
    zz = _C(lambda2, ll2)
    z2 = _scal(c[0], zz)
    for k in range(1, len(c)):
        z2 = _add(z2, _scal(c[k], _csin(_scal(float(2 * k), zz))))
    z2 = _scal(n, z2)
    return Utm(utm_zone=z, utm_x=_XS + z2.im, utm_y=z2.re)


def of_utm(u):
    """Latlong.of_utm WGS84"""
    e = WGS84.e
    n = _K0 * WGS84.a
    c = _C_UTM_INV
    ys = 0.0
    x, y, f = u.utm_x, u.utm_y, u.utm_zone
    if x < 0.0 or x > 1e7 or y < -1e7 or y > 1e7 or f < 0 or f > 60:
        raise InvalidArgument("Latlong.of_utm")
    lambda_c = deg_to_rad(float(6 * f - 183))
    z1 = _scal(1.0 / n / c[0], _C(y - ys, x - _XS))
    z = z1
    for k in range(1, len(c)):
        z = _sub(z, _scal(c[k], _csin(_scal(float(2 * k), z1))))
    ll, lls = z.re, z.im
    lam = lambda_c + cm.atan(cm.sinh(lls) / cm.cos(ll))
    phi2 = cm.asin(cm.sin(ll) / cm.cosh(lls))
    ll = latitude_isometrique(phi2, 0.0)
    phi = inverse_latitude_isometrique(ll, e, 1e-11)
    return make_geo(phi, lam)


def utm_add(u, xy):
    return Utm(utm_x=u.utm_x + xy[0], utm_y=u.utm_y + xy[1], utm_zone=u.utm_zone)


# ---------------------------------------------------------------- strings

def _str_split_space(s):
    import re
    parts = re.split(r"[ \t'\"]+", s)
    while parts and parts[0] == "":
        parts.pop(0)
    while parts and parts[-1] == "":
        parts.pop()
    return parts


def _ios(x):
    try:
        return int_of_string(x)
    except Exception:
        raise Failure("int_of_string: %s" % x)


def deg_of_string(s):
    p = _str_split_space(s)
    if len(p) == 3:
        return decimal(_ios(p[0]), _ios(p[1]), float_of_string(p[2]))
    if len(p) == 4:
        hemi = p[3]
        if hemi in ("N", "E"):
            sign = 1.0
        elif hemi in ("S", "W"):
            sign = -1.0
        else:
            raise Failure("N or S expected for hemispere in dms, found '%s'" % hemi)
        return sign * decimal(_ios(p[0]), _ios(p[1]), float_of_string(p[2]))
    if len(p) == 1:
        return float_of_string(p[0])
    raise InvalidArgument("Latlong.of_string: %s" % s)


# ---------------------------------------------------------------- ECEF / NED

def ecef_of_geo(g, h):
    elps = WGS84
    e2 = 2.0 * elps.df - elps.df * elps.df
    lat, long = g.posn_lat, g.posn_long
    sin_lat = cm.sin(lat)
    cos_lat = cm.cos(lat)
    cos_long = cm.cos(long)
    chi = cm.sqrt(1.0 - e2 * sin_lat * sin_lat)
    a_chi = elps.a / chi
    x = (a_chi + h) * cos_lat * cos_long
    y = (a_chi + h) * cos_lat * cm.sin(long)
    z = (a_chi * (1.0 - e2) + h) * sin_lat
    return [x, y, z]


def geo_of_ecef(ecef):
    elps = WGS84
    e2 = 2.0 * elps.df - elps.df * elps.df
    ep2 = elps.df * (2.0 - elps.df) / cm.pow(1.0 - elps.df, 2.0)
    b = elps.a * (1.0 - elps.df)
    x, y, z = ecef[0], ecef[1], ecef[2]
    z2 = cm.pow(z, 2.0)
    r2 = cm.pow(x, 2.0) + cm.pow(y, 2.0)
    r = cm.sqrt(r2)
    _E2 = cm.pow(elps.a, 2.0) - cm.pow(b, 2.0)
    _F = 54.0 * cm.pow(b, 2.0) * z2
    _G = r2 + (1.0 - e2) * z2 - e2 * _E2
    _Cc = (e2 * e2 * _F * r2) / cm.pow(_G, 3.0)
    _S = cm.pow(1.0 + _Cc + cm.sqrt(cm.pow(_Cc, 2.0) + 2.0 * _Cc), 1.0 / 3.0)
    _P = _F / (3.0 * cm.pow(_S + 1.0 / _S + 1.0, 2.0) * cm.pow(_G, 2.0))
    _Q = cm.sqrt(1.0 + 2.0 * e2 * e2 * _P)
    r0 = (-(e2 * _P * r)) / (1.0 + _Q) + cm.sqrt((cm.pow(elps.a, 2.0) / 2.0) * (1.0 + 1.0 / _Q)
                                                  - ((1.0 - e2) * _P * z2) / (_Q * (1.0 + _Q)) - _P * r2 / 2.0)
    tmp = cm.pow(r - e2 * r0, 2.0)
    _U = cm.sqrt(tmp + z2)
    _V = cm.sqrt(tmp + (1.0 - e2) * z2)
    z0 = (cm.pow(b, 2.0) * z) / (elps.a * _V)
    h = _U * (1.0 - cm.pow(b, 2.0) / (elps.a * _V))
    phi = cm.atan((z + ep2 * z0) / r)
    lam = cm.atan2(y, x)
    return Geo(posn_lat=phi, posn_long=lam), h


def ned_of_ecef(r, p):
    g, _h = geo_of_ecef(r)
    sin_lambda = cm.sin(g.posn_long)
    cos_lambda = cm.cos(g.posn_long)
    sin_phiP = cm.sin(g.posn_lat)
    cos_phiP = cm.cos(g.posn_lat)
    x_xr = p[0] - r[0]
    y_yr = p[1] - r[1]
    z_zr = p[2] - r[2]
    e = -sin_lambda * x_xr + cos_lambda * y_yr
    n = -sin_phiP * cos_lambda * x_xr - sin_phiP * sin_lambda * y_yr + cos_phiP * z_zr
    u = cos_phiP * cos_lambda * x_xr + cos_phiP * sin_lambda * y_yr + sin_phiP * z_zr
    return [n, e, -u]


def ecef_of_ned(r, ned):
    g, _h = geo_of_ecef(r)
    sin_lambda = cm.sin(g.posn_long)
    cos_lambda = cm.cos(g.posn_long)
    sin_phiP = cm.sin(g.posn_lat)
    cos_phiP = cm.cos(g.posn_lat)
    n, e, u = ned[0], ned[1], -ned[2]
    x = -sin_lambda * e - cos_lambda * sin_phiP * n + cos_lambda * cos_phiP * u + r[0]
    y = cos_lambda * e - sin_lambda * sin_phiP * n + cos_phiP * sin_lambda * u + r[1]
    z = cos_phiP * n + sin_phiP * u + r[2]
    return [x, y, z]


def int64_of_float(x):
    """Int64.of_float: C cast (truncation); out of range/nan -> INT64_MIN."""
    if math.isnan(x) or math.isinf(x) or abs(x) >= 2.0 ** 63:
        return -(1 << 63)
    return int(x)


# ---------------------------------------------------------------- 2D

def vect_add(u, v):
    return (u[0] + v[0], u[1] + v[1])


def polar2cart(r, theta):
    return (r * cm.cos(theta), r * cm.sin(theta))


# ---------------------------------------------------------------- files

_EXTENSIONS = ["", ".gz", ".Z", ".bz2", ".zip", ".ZIP"]


def find_file(path, file):
    for p in path:
        for ext in _EXTENSIONS:
            f = _concat(p, file) + ext
            if os.path.exists(f):
                return f
    raise NotFound(file)


def read_compress(file):
    """Ocaml_tools.open_compress, whole content as bytes."""
    if file.endswith((".gz", ".Z", ".zip", ".ZIP")):
        return subprocess.run(["sh", "-c", "gunzip -c " + file], capture_output=True).stdout
    if file.endswith(".bz2"):
        with bz2.open(file, "rb") as f:
            return f.read()
    with open(file, "rb") as f:
        return f.read()


# ---------------------------------------------------------------- EGM96

_EGM_NCOLS, _EGM_NROWS = 1440, 721
_egm96 = None


def egm96_of_wgs84(g):
    global _egm96
    if _egm96 is None:
        f = find_file([_concat(env.paparazzi_home, "data", "srtm")], "WW15MGH.DAC")
        data = read_compress(f)
        n = _EGM_NCOLS * _EGM_NROWS * 2
        if len(data) < n:
            raise EOFError("End_of_file")
        _egm96 = data[:n]
    lat = truncate(rad_to_deg(norm_angle(g.posn_lat)))
    lon = truncate(rad_to_deg(norm_angle(g.posn_long)))
    ilat = (90 - lat) * 4
    ilon = (lon + (360 if lon < 0 else 0)) * 4
    i = 2 * (ilat * _EGM_NCOLS + ilon)
    x = (_egm96[i] << 8) + _egm96[i + 1]
    # (x lsl 16) asr 16 is the identity on 63-bit OCaml ints: no sign extension
    return float(x) / 100.0


# ---------------------------------------------------------------- SRTM

class TileNotFound(Exception):
    pass


_TILE_COEF = 1200.0
_GL3, _GL1 = 1201, 3601
_htiles = {}
_srtm_path = [".", env.srtm_pprzgcs_path, env.srtm_path]


def _srtm_find(tile):
    if tile in _htiles:
        return _htiles[tile]
    bottom, left = tile
    tile_name = "%s%.0f%s%03.0f" % ("N" if bottom >= 0.0 else "S", abs(bottom),
                                   "E" if left >= 0.0 else "W", abs(left))
    try:
        f = find_file(_srtm_path, tile_name + ".SRTMGL1.hgt")
        n, factor = _GL1 * _GL1 * 2, 3.0
    except NotFound:
        try:
            f = find_file(_srtm_path, tile_name + ".hgt")
            n, factor = _GL3 * _GL3 * 2, 1.0
        except NotFound:
            raise TileNotFound(tile_name)
    buf = read_compress(f)
    if len(buf) < n:
        raise EOFError("End_of_file")
    buf = buf[:n]
    _htiles[tile] = (buf, factor)
    return buf, factor


def srtm_of_wgs84(g):
    lat = rad_to_deg(g.posn_lat)
    long = rad_to_deg(g.posn_long)
    bottom, left = cm.floor(lat), cm.floor(long)
    tile, factor = _srtm_find((bottom, left))
    x = truncate((long - left) * factor * _TILE_COEF + 0.5)
    y = truncate((lat - bottom) * factor * _TILE_COEF + 0.5)
    tile_size = truncate(factor * _TILE_COEF + 1.0)
    pos = 2 * ((tile_size - y) * tile_size + x)
    b0, b1 = tile[pos], tile[pos + 1]
    return (((b0 & 127) << 8) | b1) - ((b0 >> 7) * 256 * 128)


def srtm_available(g):
    try:
        srtm_of_wgs84(g)
        return True
    except Exception:
        return False
