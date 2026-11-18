#!/usr/bin/python3
"""Convert an SD-card telemetry log (pprzlog format) into a server-like log
(.data + .log) and keep the original as .tlm.

Python replacement of the OCaml `sd2log`, same usage:
  sd2log.py <telemetry airborne file> [<output directory (default var/logs)>]
"""

import os
import shutil
import struct
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib", "python"))

from pprzgen import xml_light as X, ext_xml, env, geo  # noqa: E402
from pprzgen import aircraft as AC  # noqa: E402
from pprzgen import pprzlink as PL  # noqa: E402
from pprzgen.ocaml import concat, string_of_float, Failure, NotFound  # noqa: E402
from pprzground.messages import MessageClass, int_assoc, assoc, int_of_value  # noqa: E402
from pprzground.transport import PprzlogTransport  # noqa: E402

VAR_PATH = concat(env.paparazzi_home, "var")
DEFAULT_LOGS_PATH = concat(VAR_PATH, "logs")
BUFFER_LEN = 2048

GPS_EPOCH = 315964800.0
LEAPS = [46828800., 78364801., 109900802., 173059203., 252028804., 315187205., 346723206., 393984007.,
         425520008., 457056009., 504489610., 551750411., 599184012., 820108813., 914803214., 1025136015.,
         1119744016., 1167264017.]


def unix_time_of_tow(tow, week=None):
    if week is None:
        from pprzground.server_core import unix_time_of_tow as u
        return u(tow)
    gps_seconds = GPS_EPOCH + float(week) * 60.0 * 60.0 * 24.0 * 7.0 + float(tow)
    return gps_seconds - float(sum(1 for x in LEAPS if gps_seconds >= x))


def hex_of_int_array(v):
    if v[0] != "array":
        raise Failure("Error: expecting array in PprzLink.hex_of_int_array")
    return "".join("%02x" % int_of_value(e) for e in v[1])


def search_conf(md5):
    d = concat(VAR_PATH, "conf")
    for f in os.listdir(d):  # Sys.readdir order
        if len(f) > 19 + 32 and f[19:19 + 32] == md5:
            return concat(d, f)
    raise NotFound(md5)


def log_xml(ac_id):
    conf_xml = ext_xml.parse_file(concat(env.paparazzi_home, "conf", "conf.xml"))
    try:
        conf_ac = ext_xml.child(conf_xml, "aircraft", select=lambda x: ext_xml.int_attrib(x, "ac_id") == ac_id)
    except NotFound:
        raise Failure("Error: A/C %d not found in conf.xml" % ac_id)
    ac = AC.parse_aircraft("", conf_ac, parse_all=True)
    expanded = X.Element(X.tag(conf_xml), X.attribs(conf_xml), ac.xml)
    return X.Element("configuration", [], [expanded, PL.messages_xml()])


def convert_file(file, output_dir=None):
    tm, dl = MessageClass("telemetry"), MessageClass("datalink")
    fd, tmp_file = tempfile.mkstemp(prefix="tlm_from_sd", suffix="data")
    os.close(fd)
    logs_path = output_dir if output_dir is not None else DEFAULT_LOGS_PATH
    st = {"start": None, "md5": "", "ac": -1}
    out = open(tmp_file, "w", encoding="latin-1", newline="")

    def use_payload(p):
        try:
            source = p[0]
            timestamp = struct.unpack_from("<i", p, 1)[0]
            data = p[5:]
            if source > 1:
                sys.stderr.write("Invalid source (%d), skipping message\n" % source)
                return
            cls = tm if source == 0 else dl
            header, vs = cls.values_of_payload(data)
            msg_id, ac_id = header["message_id"], header["sender_id"]
            if source == 0 and st["ac"] < 0:
                st["ac"] = ac_id
            if ac_id != st["ac"] and source == 0:
                sys.stderr.write("Discarding message with ac_id %d, previous one was %d\n" % (ac_id, st["ac"]))
                return
            descr = cls.message_of_id(msg_id)
            t = float(timestamp) / 1e4
            out.write("%.4f %d %s\n" % (t, ac_id, cls.string_of_message(descr, vs)))
            if source == 0:
                n = descr.name
                if n == "GPS" and st["start"] is None and (int_assoc("mode", vs) == 3 or int_assoc("week", vs) > 0):
                    itow = int(int_assoc("itow", vs) / 1000)
                    st["start"] = unix_time_of_tow(itow, int_assoc("week", vs)) - t
                elif n == "GPS_INT" and st["start"] is None and int_assoc("fix", vs) >= 3:
                    st["start"] = unix_time_of_tow(int(int_assoc("tow", vs) / 1000)) - t
                elif n == "ALIVE" and st["md5"] == "":
                    st["md5"] = hex_of_int_array(assoc("md5sum", vs))
        except Exception:
            sys.stderr.write("Parsing error, skipping message\n")

    parser = PprzlogTransport()
    buf = b""
    with open(file, "rb") as f_in:
        while True:
            data = f_in.read(BUFFER_LEN - len(buf))
            if not data:
                break
            buf += data
            while buf:
                used = parser.parse(use_payload, buf)
                if used > 0:
                    buf = buf[used:]
                elif len(buf) == BUFFER_LEN:
                    buf = buf[1:]
                else:
                    break
    out.close()

    sys.stderr.write("Renaming produced file ...\n")
    if st["start"] is None:
        sys.stderr.write("Warning: not time found in GPS messages; using current date\n")
        start_time, mark = time.time(), "_no_GPS"
    else:
        start_time, mark = st["start"], ""
    d = time.localtime(start_time)
    basename = "%02d_%02d_%02d__%02d_%02d_%02d_SD%s" % (d.tm_year % 100, d.tm_mon, d.tm_mday,
                                                        d.tm_hour, d.tm_min, d.tm_sec, mark)
    data_name, log_name, tlm_name = basename + ".data", basename + ".log", basename + ".tlm"
    shutil.move(tmp_file, concat(logs_path, data_name))
    sys.stderr.write("%s file produced\n" % data_name)
    sys.stderr.write("Looking for %s conf...\n" % st["md5"])
    try:
        configuration = X.parse_string(geo.read_compress(search_conf(st["md5"])).decode("latin-1"))
    except (NotFound, OSError):
        sys.stderr.write("Not found...\n")
        configuration = None
        if st["ac"] >= 0:
            sys.stderr.write("Try to rebuild it for A/C %d ...\n" % st["ac"])
            try:
                configuration = log_xml(st["ac"])
            except Exception:
                sys.stderr.write("Failure: A/C %d not found\n" % st["ac"])
                configuration = None
    if configuration is not None:
        log = ext_xml.subst_attrib("time_of_day", string_of_float(start_time),
                                   ext_xml.subst_attrib("data_file", data_name, configuration))
        with open(concat(logs_path, log_name), "w", encoding="latin-1", newline="") as f:
            f.write(X.to_string_fmt(log))
        sys.stderr.write("%s file produced\n" % log_name)
    else:
        sys.stderr.write("No .log produced\n")
    shutil.copy(file, concat(logs_path, tlm_name))
    sys.stderr.write("%s file saved\n" % tlm_name)


def main():
    if len(sys.argv) == 2:
        convert_file(sys.argv[1])
    elif len(sys.argv) == 3:
        convert_file(sys.argv[1], sys.argv[2])
    else:
        sys.stderr.write("Usage: %s <telemetry airborne file> [<output directory (default in Paparazzi logs folder)>]\n"
                         % sys.argv[0])
        sys.exit(1)


if __name__ == "__main__":
    main()
