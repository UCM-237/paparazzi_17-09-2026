open Latlong
let p f = Printf.printf "%Lx\n" (Int64.bits_of_float f)
let () =
  Random.init 5;
  for k = 1 to 400 do
    let lat = Random.float 120. -. 60. and lon = Random.float 300. -. 150. in
    let x = Random.float 2000. -. 1000. and y = Random.float 2000. -. 1000. in
    Printf.printf "I %Lx %Lx %Lx %Lx\n" (Int64.bits_of_float lat) (Int64.bits_of_float lon) (Int64.bits_of_float x) (Int64.bits_of_float y);
    let g = make_geo_deg lat lon in
    let g1 = Navref.add_pos_to_nav_ref (Navref.Geo g) (x, y) in p g1.posn_lat; p g1.posn_long;
    let u = utm_of WGS84 g in
    let g2 = Navref.add_pos_to_nav_ref (Navref.Utm u) (x, y) in p g2.posn_lat; p g2.posn_long;
    let e = ecef_of_geo WGS84 g 100. in
    let g3 = Navref.add_pos_to_nav_ref (Navref.Ltp e) (x, y) in p g3.posn_lat; p g3.posn_long;
    let q = Quaternion.multiply_quaternion (Quaternion.quaternion_from_angle (x /. 1000.) (y /. 1000.) (lat /. 50.)) (Quaternion.quaternion_from_angle 0.1 (-0.3) (lon /. 100.)) in
    p q.Quaternion.w; p q.Quaternion.x; p q.Quaternion.y; p q.Quaternion.z;
    let a = Quaternion.quaternion_to_angle q in p a.Quaternion.r; p a.Quaternion.p; p a.Quaternion.y;
    (* wind: 12 samples around a circle with a wind of (x,y)/200 *)
    let id = string_of_int k in
    Windcore.new_ac id 36;
    for i = 0 to 11 do
      let c = float i *. 0.52 +. 0.01 in
      let air = 12. in
      let wx = x /. 200. and wy = y /. 200. in
      let th = Geometry_2d.heading_of_to_angle_rad c in
      let gx = air *. cos th +. wx and gy = air *. sin th +. wy in
      let gs = sqrt (gx *. gx +. gy *. gy) and gc = Geometry_2d.heading_of_to_angle_rad (atan2 gy gx) in
      Windcore.update id gs gc
    done;
    let (cap, ws, mean, sd, n) = Windcore.get id in p cap; p ws; p mean; p sd; Printf.printf "%d\n" n
  done
