#!/usr/bin/env python3
"""Write public/overlay/THIRD_PARTY.md from vendor/LOCK.md. Run it after a change to vendor/LOCK.md.

Usage: python3 scripts/gen_third_party.py [--check]
With --check it exits 1 when the file is out of date.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "public" / "overlay" / "THIRD_PARTY.md"
HEAD = """# Third-party software

free-agent-fleet is MIT licensed. It installs and calls the software below. Each part keeps its own license.
The list comes from `vendor/LOCK.md`, which records every install with its source and pinned version.

Some licenses need your attention:

%s

| Name | Kind | Source | Version | License |
|---|---|---|---|---|
"""
FLAGS = (("AGPL", "AGPL: run it as a separate service. Do not copy its code into this project."),
         ("BUSL", "BUSL: free to use for your own work. Read the terms before you offer it to others."),
         ("Sustainable Use", "Sustainable Use License (n8n): internal business use only. Do not offer n8n to others as a hosted service. Paid enterprise features stay off."),
         ("GPL", "GPL: run it as a separate tool. Do not copy its code into this project."),
         ("Elastic", "Elastic License: read the terms before you use it in a product."))


def rows():
    out = []
    for line in (ROOT / "vendor" / "LOCK.md").read_text().splitlines():
        if not line.startswith("|") or line.startswith("| Name") or re.match(r"^\|[-| ]+\|$", line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 6:
            out.append(cells)
    return out


def render():
    table = rows()
    notes = []
    for _name, kind, _source, _version, _sha, license_ in table:
        if kind == "apt":
            continue                                   # system packages from the distribution need no special note
        for key, text in FLAGS:
            if key in license_ and key != "GPL" or (key == "GPL" and re.search(r"(?<!A)GPL", license_)):
                if "- " + text not in notes:
                    notes.append("- " + text)
    notes = notes or ["- None of the listed licenses needs special care."]
    body = HEAD % "\n".join(notes)
    for name, kind, source, version, _sha, license_ in sorted(table, key=lambda r: r[0].lower()):
        source = source.split(" (")[0][:90]
        body += "| %s | %s | %s | %s | %s |\n" % (name, kind, source, version[:40], license_)
    return body


def main(argv):
    text = render()
    if "--check" in argv:
        return 0 if OUT.exists() and OUT.read_text() == text else 1
    OUT.write_text(text)
    print("wrote %s (%d rows)" % (OUT.relative_to(ROOT), len(rows())))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
