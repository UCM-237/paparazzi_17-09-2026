#!/usr/bin/python3
"""Print, for each message class of var/messages.xml, the free message ids
(1..255). Python replacement of the OCaml find_free_msg_id.out."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib", "python"))

from pprzgen import xml_light as X, ext_xml, env  # noqa: E402
from pprzgen.ocaml import concat  # noqa: E402

NB_MSG = 255


def message_ids(x):
    if X.tag(x) == "message":
        return [int(X.attrib(x, "id"))]
    return [i for c in X.children(x) for i in message_ids(c)]


def main():
    xml = ext_xml.parse_file(concat(env.paparazzi_home, "var", "messages.xml"))
    classes = X.children(xml)
    used = {}
    for c in classes:  # List.assoc: the first class of a given name wins
        used.setdefault(X.attrib(c, "name"), message_ids(c))
    out = []
    for c in classes:
        u = set(used[X.attrib(c, "name")])
        out.append("Class : %s \n" % X.attrib(c, "name"))
        out.append("".join(" %d," % i for i in range(1, NB_MSG + 1) if i not in u))
        out.append("\n\n")
    sys.stdout.write("".join(out))


if __name__ == "__main__":
    main()
