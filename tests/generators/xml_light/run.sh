#!/bin/sh
# Differential test: OCaml xml-light vs sw/lib/python/pprzgen/xml_light.py
# on every XML file under conf/ (with and without DTD validation).
# Requires OCaml + xml-light (only while the OCaml version still exists).
set -e
here=$(cd "$(dirname "$0")" && pwd)
home=$(cd "$here/../../.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
cp "$here/dump.ml" "$tmp/"
(cd "$tmp" && ocamlfind ocamlopt -package xml-light -linkpkg dump.ml -o dump.native >/dev/null 2>&1)
cd "$home"
find conf -name '*.xml' -not -path '*/var/*' | sort > "$tmp/files"
for mode in "" "-noprove"; do
  xargs "$tmp/dump.native" $mode < "$tmp/files" > "$tmp/o.out"
  xargs python3 -I "$here/dump.py" "$home/sw/lib/python" $mode < "$tmp/files" > "$tmp/p.out"
  if ! diff -q "$tmp/o.out" "$tmp/p.out" >/dev/null; then
    echo "MISMATCH (mode '$mode'):"; diff "$tmp/o.out" "$tmp/p.out" | head -20; exit 1
  fi
  echo "mode '${mode:-prove}': $(wc -l < "$tmp/files") files identical"
done
