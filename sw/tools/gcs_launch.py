#! /usr/bin/env python3
from optparse import OptionParser, OptionGroup, OptionValueError
import subprocess
from os import getenv, path, execvp

HOME = getenv("PAPARAZZI_HOME", path.normpath(path.join(path.dirname(path.abspath(__file__)), '../../')))


def pprzgcs_help(option, opt, value, parser):
    try:
        cp = subprocess.run(["pprzgcs", "-h"], capture_output=True)
        # trim to relevant output
        lines = cp.stdout.decode().split("\n")[4:]
        options = "\n".join(lines)
        print("PprzGCS options:\n\n" + options)
        exit(0)
    except FileNotFoundError:
        print("PprzGCS not found!")
        exit(1)


def main():

    usage = "usage: %prog -g <GCS type> -- [GCS arguments]\n" + \
            "Run %prog --help to list the options."
    parser = OptionParser(usage)

    parser.add_option("-g", "--gcs", dest="gcstype",
                      type='choice', choices=['pprzgcs'],
                      action="store", help="GCS type to start: pprzgcs (the legacy OCaml GCS has been removed)")
    parser.add_option("--pprzgcs_help", dest="pprzgcs_help", action="callback", callback=pprzgcs_help,
                      help="Print help for pprzgcs")

    (options, args) = parser.parse_args()

    def run_gcs(cmd, args, error_msg):
        try:
            args = [cmd] + args
            print("Running \"" + " ".join(args) + "\"")
            execvp(cmd, args)
        except FileNotFoundError:
            print(error_msg)

    run_gcs("pprzgcs", args, "PprzGCS not found!")


if __name__ == "__main__":
    main()



