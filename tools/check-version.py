#!/usr/bin/env python3
"""Checks that everything carrying the release number says the same thing.

A release touches four files that nothing connects to each other, so forgetting
one is silent. v1.8 forgot two of them: the `version` file said v1.8 while
VERSION in the driver still said 1.7, so the card and /Mgmt/ProcessVersion
reported the previous release for a week, and the v1.8 entry went only into
ChangeLog - the Package manager shows `changes`, which still ended at v1.7.
Neither is visible from the code; both are visible from here.

The firmware version is deliberately not checked here. It moves only when the
sketch changes, which a driver-only release must not force; tools/check-hex.py
owns that one.

    python3 tools/check-version.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

VERSION_FILE = os.path.join(REPO, "version")
DRIVER = os.path.join(REPO, "dbus-maxxfan.py")
CHANGELOGS = ["changes", "ChangeLog"]


def package_version():
    """What the Package manager reads. Carries a leading v."""
    with open(VERSION_FILE) as fh:
        raw = fh.read().strip()
    if not re.match(r"^v[0-9]+\.[0-9]+", raw):
        raise SystemExit("version says %r, expected something like v1.8" % raw)
    return raw


def driver_version():
    """What the driver publishes as /Mgmt/ProcessVersion. No leading v."""
    with open(DRIVER) as fh:
        m = re.search(r'^VERSION\s*=\s*"([^"]+)"', fh.read(), re.MULTILINE)
    if not m:
        raise SystemExit("no VERSION in %s" % DRIVER)
    return m.group(1)


def top_entry(path):
    """The version heading a changelog opens with, or None."""
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                return line
    return None


def main():
    want = package_version()
    problems = []

    got = driver_version()
    if got != want.lstrip("v"):
        problems.append(
            "version says %s but VERSION in dbus-maxxfan.py says %s - the "
            "card would report the wrong release" % (want, got))

    for name in CHANGELOGS:
        path = os.path.join(REPO, name)
        if not os.path.exists(path):
            continue
        head = top_entry(path)
        if head != want:
            problems.append(
                "%s opens with %r, expected %s - this release has no entry "
                "where it is read" % (name, head, want))

    for p in problems:
        print("error: %s" % p)
    if problems:
        return 1
    print("release %s: driver, changes and ChangeLog all agree" % want)
    return 0


if __name__ == "__main__":
    sys.exit(main())
