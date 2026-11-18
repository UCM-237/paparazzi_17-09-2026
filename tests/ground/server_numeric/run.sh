#!/bin/sh
# Bit-exact comparison of the server's numeric code (add_pos_to_nav_ref on
# geo/utm/ltp references, quaternions, wind estimation) with the OCaml
# sources (compiled without Ivy). Requires OCaml while it is still installed.
set -e
here=$(cd "$(dirname "$0")" && pwd); home=$(cd "$here/../../.." && pwd); t=$(mktemp -d)
trap 'rm -rf "$t"' EXIT
cp "$home"/sw/lib/ocaml/geometry_2d.ml "$home"/sw/lib/ocaml/latlong.ml "$home"/sw/lib/ocaml/quaternion.ml \
   "$home"/sw/lib/ocaml/env.ml "$home"/sw/lib/ocaml/ocaml_tools.ml "$t"/
sed -n '/^open Geometry_2d/,$p' "$home"/sw/ground_segment/tmtc/wind.ml > "$t"/windcore.ml
sed -i '1i open Geometry_2d' "$t"/windcore.ml
sed -n '/^let add_pos_to_nav_ref/,/^type waypoint/p' "$home"/sw/ground_segment/tmtc/aircraft_server.ml | sed '$d' > "$t"/navref.ml
sed -i '1i open Latlong\ntype nav_ref = Geo of Latlong.geographic | Utm of Latlong.utm | Ltp of Latlong.ecef' "$t"/navref.ml
cp "$here"/oracle.ml "$t"/t.ml
(cd "$t" && ocamlfind ocamlc -package str,unix,xml-light -linkpkg geometry_2d.ml ocaml_tools.ml env.ml latlong.ml \
   quaternion.ml windcore.ml navref.ml t.ml -o t >/dev/null 2>&1 && PAPARAZZI_HOME="$home" ./t > o.txt)
/usr/bin/python3 "$here"/model.py "$t"/o.txt > "$t"/p.txt
n=$(diff "$t"/o.txt "$t"/p.txt | grep -c '^<' || true)
echo "$(wc -l < "$t"/o.txt) values compared, $n differences"
[ "$n" = 0 ]
