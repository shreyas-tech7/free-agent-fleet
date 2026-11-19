#!/usr/bin/env python3
"""Copy one skill from a downloaded repository into skills/<name>/ with provenance.

Usage:
  vendor_skill.py --src DIR --repo-url URL --commit SHA --license MIT --license-file FILE \
                  --skill-dir RELATIVE_DIR --name NAME [--out skills] [--notice-file FILE]

Rules (R6, R7, G5):
- The source is data. This tool never runs anything from it.
- Only text files are copied: SKILL.md and files under references/, templates/, examples/.
  Scripts, assets, and binary files are left out.
- Each copied file must be 15 KB or less. A larger Markdown file is split at its `## ` headings.
  The first part stays in the original file. The other parts go to references/.
- skills/<name>/UPSTREAM.md records the source URL, the commit, the license, and the SHA-256 of
  each original file, so a reader can compare the copy with the source.
"""
import argparse
import hashlib
import pathlib
import re
import shutil
import sys

LIMIT = 15 * 1024
PART_TARGET = 13 * 1024
ALLOWED_DIRS = ("references", "templates", "examples")
TEXT_SUFFIXES = (".md", ".txt", ".yaml", ".yml", ".json")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def split_markdown(text):
    """Split text at `## ` headings into parts of about PART_TARGET bytes. Returns a list of (title, text)."""
    pieces = re.split(r"(?m)^(?=## )", text)
    pieces = [p for p in pieces if p]
    out, current, size = [], "", 0
    for piece in pieces:
        if len(piece.encode()) > PART_TARGET:
            sub = re.split(r"(?m)^(?=### )", piece)
            if len(sub) == 1:
                raise ValueError("a section is larger than %d bytes and has no ### headings" % PART_TARGET)
            parts = sub
        else:
            parts = [piece]
        for part in parts:
            n = len(part.encode())
            if n > PART_TARGET:
                raise ValueError("a subsection is larger than %d bytes" % PART_TARGET)
            if current and size + n > PART_TARGET:
                out.append(current)
                current, size = "", 0
            current += part
            size += n
    if current:
        out.append(current)
    return out


def title_of(part):
    m = re.search(r"(?m)^#{2,3} (.+)$", part)
    return m.group(1).strip() if m else "more"


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "part"


def write_split(dest_main, dest_refs, text, base):
    """Write text to dest_main, spilling parts to dest_refs. Returns the list of reference files."""
    if len(text.encode()) <= LIMIT:
        dest_main.write_text(text)
        return []
    parts = split_markdown(text)
    first, rest = parts[0], parts[1:]
    refs = []
    dest_refs.mkdir(parents=True, exist_ok=True)
    for i, part in enumerate(rest, 1):
        name = "%s-part-%d-%s.md" % (base, i, slug(title_of(part)))
        (dest_refs / name).write_text(part)
        refs.append((name, title_of(part)))
    index = "\n\n## More detail in the references folder\n\nThis file was split to stay under 15 KB. The text is unchanged. Read a file when the task needs it.\n\n"
    index += "".join("- `references/%s`: starts at \"%s\"\n" % (n, t) for n, t in refs)
    dest_main.write_text(first.rstrip("\n") + index)
    return refs


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--repo-url", required=True)
    ap.add_argument("--commit", required=True)
    ap.add_argument("--license", required=True)
    ap.add_argument("--license-file", required=True)
    ap.add_argument("--skill-dir", required=True, help="folder of the skill inside the repository ('.' for the root)")
    ap.add_argument("--name", required=True)
    ap.add_argument("--out", default="skills")
    ap.add_argument("--replace", action="store_true", help="replace a copy that already exists (a copy is reproducible from the pinned source)")
    ap.add_argument("--notice-file", help="the NOTICE file of the source. The Apache-2.0 license says that it must travel with a copy")
    args = ap.parse_args(argv)
    src = pathlib.Path(args.src).resolve() / args.skill_dir
    out = pathlib.Path(args.out) / args.name
    if not (src / "SKILL.md").is_file():
        print("no SKILL.md in %s" % src, file=sys.stderr)
        return 2
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,50}", args.name):
        print("bad skill name", file=sys.stderr)
        return 2
    if out.exists():
        if not args.replace:
            print("%s exists. Pass --replace to make it again from the pinned source." % out, file=sys.stderr)
            return 2
        shutil.rmtree(out)
    out.mkdir(parents=True)
    rows, omitted, split = [], [], []

    def take(rel, data):
        rows.append((rel, sha(data), len(data)))

    main_bytes = (src / "SKILL.md").read_bytes()
    take("SKILL.md", main_bytes)
    refs = write_split(out / "SKILL.md", out / "references", main_bytes.decode(), "skill")
    if refs:
        split.append("SKILL.md into %d files" % (len(refs) + 1))
    for sub in ALLOWED_DIRS:
        base = src / sub
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            rel = str(path.relative_to(src))
            if path.suffix.lower() not in TEXT_SUFFIXES:
                omitted.append("%s (not a text file)" % rel)
                continue
            data = path.read_bytes()
            try:
                text = data.decode()
            except UnicodeDecodeError:
                omitted.append("%s (not UTF-8)" % rel)
                continue
            take(rel, data)
            target = out / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if len(data) <= LIMIT:
                target.write_text(text)
            elif path.suffix.lower() == ".md":
                try:
                    got = write_split(target, target.parent, text, path.stem)
                    split.append("%s into %d files" % (rel, len(got) + 1))
                except ValueError as exc:
                    omitted.append("%s (too large: %s)" % (rel, exc))
                    if target.exists():
                        target.unlink()
            else:
                omitted.append("%s (over 15 KB)" % rel)
    shutil.copyfile(args.license_file, out / "LICENSE")
    notice = None
    if args.notice_file:
        notice = pathlib.Path(args.notice_file).read_bytes()
        if len(notice) > LIMIT:
            print("the NOTICE file is over 15 KB", file=sys.stderr)
            return 2
        (out / "NOTICE").write_bytes(notice)
    lines = ["# Upstream", "", "This skill is a copy of a third-party skill. Do not edit it here. Replace it through the security agent's install pipeline.", "",
             "- Source: %s" % args.repo_url, "- Commit: %s" % args.commit, "- Folder in the source: %s" % args.skill_dir,
             "- License: %s (see LICENSE)" % args.license, "- Updates: off. A new version needs a new pinned commit.", "",
             "## What changed", ""]
    lines += ["- Only text files were copied. Scripts and assets were left out."]
    lines += ["- Split to stay under 15 KB: %s" % "; ".join(split) if split else "- No file needed a split."]
    lines += ["- Left out: %s" % "; ".join(omitted) if omitted else "- Nothing else was left out."]
    if notice is not None:
        lines += ["- The NOTICE file of the source is kept as `NOTICE` (SHA-256 %s)." % sha(notice)]
    lines += ["", "## Original files", "", "| File | Bytes | SHA-256 of the original |", "|---|---|---|"]
    lines += ["| %s | %d | %s |" % (rel, n, h) for rel, h, n in rows]
    (out / "UPSTREAM.md").write_text("\n".join(lines) + "\n")
    print("vendored %s: %d original files, %d split, %d left out" % (args.name, len(rows), len(split), len(omitted)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
