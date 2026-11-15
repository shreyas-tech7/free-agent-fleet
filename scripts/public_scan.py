#!/usr/bin/env python3
"""Scan a folder for secrets, private addresses, personal paths, and token shapes. The public CI runs it.

Usage: python3 scripts/public_scan.py [PATH]
Exit 0 when the folder is clean. Exit 1 and a list of file, line, and rule when it is not. It never prints the matched text.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ops.public import scans  # noqa: E402


def main(argv):
    target = pathlib.Path(argv[0] if argv else ROOT)
    allow = scans.load_allow(ROOT / "public" / "scan-allow.txt")
    found = scans.scan_tree(target, terms=(), allow=allow)
    for rel, line, rule in found:
        print("%s:%d %s" % (rel, line, rule))
    print("public_scan: %d finding(s)" % len(found))
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
