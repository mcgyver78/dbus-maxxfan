#!/usr/bin/env python3
"""Checks that the firmware shipped in the package matches the sketch.

The dangerous drift is a stale arduino/maxxfan_tx.hex: the card would then
offer an "update" that installs an older sketch than the one in the repository.

Comparing the .hex byte for byte against a fresh build would catch that, but it
also fails whenever the toolchain version moves, which says nothing about the
firmware. So instead this compares the version string, which the sketch puts
into flash through its identify reply and which is therefore readable straight
out of the .hex.

    python3 tools/check-hex.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import flash                                             # noqa: E402

REPO = os.path.dirname(HERE)
INO = os.path.join(REPO, "arduino", "maxxfan_tx", "maxxfan_tx.ino")
HEX = os.path.join(REPO, "arduino", "maxxfan_tx.hex")
VER = HEX + ".ver"


def sketch_version():
    with open(INO) as fh:
        m = re.search(r'^#define\s+SKETCH_VERSION\s+"([^"]+)"', fh.read(),
                      re.MULTILINE)
    if not m:
        raise SystemExit("no SKETCH_VERSION in %s" % INO)
    return m.group(1)


def firmware_version():
    """The version the compiled image will report, read out of the image."""
    image = flash.parse_hex(HEX)
    m = re.search(rb"MAXXFAN [0-9]+ ([0-9][0-9.]*)", image)
    if not m:
        raise SystemExit("%s carries no MAXXFAN identify string - is it the "
                         "right sketch?" % HEX)
    return m.group(1).decode()


def main():
    want = sketch_version()
    problems = []

    got = firmware_version()
    if got != want:
        problems.append(
            "arduino/maxxfan_tx.hex reports %s but the sketch says %s. "
            "Rebuild it: tools/build-hex.sh" % (got, want))

    stored = open(VER).read().strip() if os.path.exists(VER) else "(missing)"
    if stored != want:
        problems.append(
            "arduino/maxxfan_tx.hex.ver says %s but the sketch says %s"
            % (stored, want))

    for p in problems:
        print("error: %s" % p)
    if problems:
        return 1
    print("firmware %s matches the sketch, and the version file agrees" % want)
    return 0


if __name__ == "__main__":
    sys.exit(main())
