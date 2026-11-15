#!/usr/bin/env python3
"""Record installs in vendor/LOCK.md (rule R6).

Each row has: name, kind, source URL, version or commit, SHA-256, license.
Running a command twice with the same name and kind updates the row.

Usage:
  lock.py add --name N --kind K --source URL --version V [--sha256 H] [--license L]
  lock.py apt PKG [PKG ...]      Record installed apt packages with their SHA-256.
  lock.py show
  lock.py merge-from PATH        Merge the rows of another LOCK.md. Its rows win.
"""
import argparse
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOCK = ROOT / "vendor" / "LOCK.md"
HEADER = """# Vendor lock

This file records every piece of software that the build installed.
Rule R6: install only from the official source, and pin an exact version or commit.
Each row names the install, its kind, its source, its pinned version or commit,
its SHA-256 when one exists, and its license.
Kinds: apt, binary, npm, pypi, docker, git, model, script.

| Name | Kind | Source | Version or commit | SHA-256 | License |
|---|---|---|---|---|---|
"""


def clean(v):
    return (v or "-").replace("|", "/").replace("\n", " ").strip() or "-"


def read_rows(path=None):
    rows = {}
    path = pathlib.Path(path) if path else LOCK
    if path.exists():
        for line in path.read_text().splitlines():
            if line.startswith("| ") and not line.startswith("| Name") and not line.startswith("|---"):
                cells = [c.strip() for c in line.strip().strip("|").split(" | ")]
                if len(cells) == 6:
                    rows[(cells[0], cells[1])] = cells
    return rows


def write_rows(rows):
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    body = "".join("| %s |\n" % " | ".join(rows[k]) for k in sorted(rows, key=lambda k: (k[1], k[0].lower())))
    LOCK.write_text(HEADER + body)
    # When root runs this, give the files back to the owner of the repo.
    if os.geteuid() == 0:
        st = ROOT.stat()
        for path in (LOCK.parent, LOCK):
            os.chown(path, st.st_uid, st.st_gid)


def upsert(name, kind, source, version, sha256="-", license_="-"):
    rows = read_rows()
    rows[(clean(name), clean(kind))] = [clean(name), clean(kind), clean(source), clean(version), clean(sha256), clean(license_)]
    write_rows(rows)


def apt_license(pkg):
    path = pathlib.Path("/usr/share/doc") / pkg / "copyright"
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return "see copyright file"
    found = re.findall(r"^License:\s*([^\n]+)", text, flags=re.M)
    seen = []
    for f in found:
        f = f.strip()
        # Keep short SPDX-like names. Skip prose.
        if re.fullmatch(r"[A-Za-z0-9.+\-]+( (or|and) [A-Za-z0-9.+\-]+)*", f) and len(f) <= 40 and f not in seen:
            seen.append(f)
        if len(seen) == 3:
            break
    return ", ".join(seen) if seen else "see copyright file"


def apt_row(pkg):
    q = subprocess.run(["dpkg-query", "-W", "-f=${Version}\t${Source}\t${Architecture}", pkg],
                       capture_output=True, text=True)
    if q.returncode != 0:
        print("lock.py: %s is not installed" % pkg, file=sys.stderr)
        return
    version = q.stdout.split("\t")[0]
    sha = "-"
    show = subprocess.run(["apt-cache", "show", "%s=%s" % (pkg, version)], capture_output=True, text=True)
    m = re.search(r"^SHA256:\s*([0-9a-f]{64})", show.stdout, flags=re.M)
    if m:
        sha = m.group(1)
    origin = "Debian or Ubuntu archive"
    pol = subprocess.run(["apt-cache", "policy", pkg], capture_output=True, text=True).stdout
    if "download.docker.com" in pol:
        origin = "https://download.docker.com/linux"
    elif "pkgs.tailscale.com" in pol:
        origin = "https://pkgs.tailscale.com/stable"
    elif "cli.github.com" in pol:
        origin = "https://cli.github.com/packages"
    upsert(pkg, "apt", origin, version, sha, apt_license(pkg))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("--name", required=True)
    a.add_argument("--kind", required=True)
    a.add_argument("--source", required=True)
    a.add_argument("--version", required=True)
    a.add_argument("--sha256", default="-")
    a.add_argument("--license", default="-")
    p = sub.add_parser("apt")
    p.add_argument("pkgs", nargs="+")
    sub.add_parser("show")
    m = sub.add_parser("merge-from")
    m.add_argument("path")
    args = ap.parse_args()
    if args.cmd == "add":
        upsert(args.name, args.kind, args.source, args.version, args.sha256, args.license)
    elif args.cmd == "apt":
        for pkg in args.pkgs:
            apt_row(pkg)
    elif args.cmd == "merge-from":
        rows = read_rows()
        rows.update(read_rows(args.path))
        write_rows(rows)
    elif args.cmd == "show":
        print(LOCK.read_text() if LOCK.exists() else "no lock file yet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
