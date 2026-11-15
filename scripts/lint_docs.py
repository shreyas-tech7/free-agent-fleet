#!/usr/bin/env python3
"""Check docs against rule R9 (Simplified Technical English).

Hard errors:
  - an em dash or en dash in prose
  - a semicolon in prose
  - a sentence longer than MAX_WORDS words
Prose means text outside fenced code blocks, inline code, tables, and HTML comments.

Usage: lint_docs.py [--list] [files...]   (default: every .md file in the repo docs set)
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAX_WORDS = 32
DOC_GLOBS = ["README.md", "HANDOFF.md", "BUILD_STATE.md", "NOTICE", "docs/*.md", "core/*.md",
             "core/policy/*.md", "agents/*/SOUL.md", "projects/*/*.md", "vendor/*.md"]


def doc_files():
    seen = []
    for g in DOC_GLOBS:
        for p in sorted(ROOT.glob(g)):
            if p.is_file() and p not in seen:
                seen.append(p)
    return seen


def prose_lines(text):
    in_fence = False
    in_comment = False
    for no, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if s.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if "<!--" in s:
            in_comment = "-->" not in s
            continue
        if in_comment:
            if "-->" in s:
                in_comment = False
            continue
        if s.startswith("|") or s.startswith("#"):
            continue
        s = re.sub(r"`[^`]*`", " ", s)                 # inline code
        s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)  # links keep their text
        s = re.sub(r"https?://\S+", " ", s)
        yield no, s


def check(path):
    problems = []
    text = path.read_text()
    for no, line in prose_lines(text):
        if "—" in line or "–" in line:
            problems.append((no, "dash character (em dash or en dash)"))
        if ";" in line:
            problems.append((no, "semicolon"))
    # Sentence length: join prose lines of one paragraph.
    para, start = [], 0
    def flush():
        nonlocal para, start
        if para:
            joined = " ".join(para)
            for sent in re.split(r"(?<=[.!?])\s+", joined):
                words = re.findall(r"[A-Za-z0-9'\-]+", sent)
                if len(words) > MAX_WORDS:
                    problems.append((start, "sentence has %d words: %s..." % (len(words), " ".join(words[:8]))))
        para = []
    prev = 0
    for no, line in prose_lines(text):
        is_item = bool(re.match(r"^\s*([-*]|\d+\.)\s+", line))
        if not line.strip() or no != prev + 1 or is_item:
            flush()
        if line.strip():
            if not para:
                start = no
            para.append(re.sub(r"^\s*([-*]|\d+\.)\s+", "", line.strip()))
        prev = no
    flush()
    return problems


def main(argv):
    files = [pathlib.Path(a).resolve() for a in argv if not a.startswith("--")] or doc_files()
    bad = 0
    for f in files:
        for no, msg in check(f):
            print("%s:%d: %s" % (f.relative_to(ROOT), no, msg))
            bad += 1
    if "--list" in argv:
        print("checked %d files" % len(files))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
