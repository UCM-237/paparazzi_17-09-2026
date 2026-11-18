import struct, sys, math
sys.path.insert(0, "/home/juan/paparazzi/sw/lib/python")
from pprzgen import geo as LL
from pprzground import server_core as SC
def b(f): return "%x" % (struct.unpack("<q", struct.pack("<d", f))[0] & 0xFFFFFFFFFFFFFFFF)
def fb(h): return struct.unpack("<d", struct.pack("<Q", int(h, 16)))[0]
out = []
p = lambda f: out.append(b(f))
wind = SC.Wind()
k = 0
for line in open(sys.argv[1]):
    if not line.startswith("I "): continue
    k += 1
    out.append(line.rstrip("\n"))
    lat, lon, x, y = [fb(h) for h in line.split()[1:]]
    g = LL.make_geo_deg(lat, lon)
    g1 = SC.add_pos_to_nav_ref(("geo", g), (x, y)); p(g1.posn_lat); p(g1.posn_long)
    u = LL.utm_of(g)
    g2 = SC.add_pos_to_nav_ref(("utm", u), (x, y)); p(g2.posn_lat); p(g2.posn_long)
    e = LL.ecef_of_geo(g, 100.0)
    g3 = SC.add_pos_to_nav_ref(("ltp", e), (x, y)); p(g3.posn_lat); p(g3.posn_long)
    q = SC.multiply_quaternion(SC.quaternion_from_angle(x / 1000.0, y / 1000.0, lat / 50.0), SC.quaternion_from_angle(0.1, -0.3, lon / 100.0))
    for v in q: p(v)
    for v in SC.quaternion_to_angle(q): p(v)
    id_ = str(k)
    wind.new_ac(id_, 36)
    for i in range(12):
        c = float(i) * 0.52 + 0.01
        th = SC.heading_of_to_angle_rad(c)
        gx = 12.0 * math.cos(th) + x / 200.0; gy = 12.0 * math.sin(th) + y / 200.0
        gs = math.sqrt(gx * gx + gy * gy); gc = SC.heading_of_to_angle_rad(math.atan2(gy, gx))
        wind.update(id_, gs, gc)
    cap, ws, mean, sd, n = wind.get(id_)
    p(cap); p(ws); p(mean); p(sd); out.append(str(n))
print("\n".join(out))
