"""Static gates of the merger. They read git objects as data and run no agent code.

G3  The change touches only paths that agents may change, and never a protected path.
G5  Each skill file is 15 KB or less.
Also: an agent branch may not add a symlink, a submodule, or a file with an unusual mode.
"""
import fnmatch
import pathlib

from ops.merger import gitops

SKILL_LIMIT_BYTES = 15 * 1024
REGULAR_MODES = {"100644", "100755"}


def load_patterns(text):
    return [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]


def matches(path, patterns):
    for pat in patterns:
        if pat.endswith("/"):
            if path.startswith(pat):
                return True
        elif fnmatch.fnmatch(path, pat):
            return True
    return False


def load_policy(repo_root):
    root = pathlib.Path(repo_root)
    protected = load_patterns((root / "core" / "policy" / "protected_paths.txt").read_text())
    writable = load_patterns((root / "core" / "policy" / "agent_writable_paths.txt").read_text())
    return protected, writable


def gate_g3(entries, protected, writable):
    """Return a list of problems. An empty list means the gate passes. `protected` and `writable` come from the LIVE repo."""
    problems = []
    for e in entries:
        path = e["path"]
        if path.startswith("/") or ".." in path.split("/") or path.startswith(".git/") or "/.git/" in path or path == ".git":
            problems.append("bad path: %s" % path)
        elif matches(path, protected):
            problems.append("protected path: %s" % path)
        elif not matches(path, writable):
            problems.append("path agents may not change: %s" % path)
        if e["status"] != "D" and e["new_mode"] not in REGULAR_MODES:
            problems.append("not a regular file (mode %s): %s" % (e["new_mode"], path))
        if e["status"] in ("T",):
            problems.append("file type change: %s" % path)
    return problems


def is_skill_file(path):
    return path.startswith("skills/") or (path.startswith("agents/") and path.endswith("/SOUL.md"))


def gate_g5(repo, rev, entries, limit=SKILL_LIMIT_BYTES):
    """Each skill file (and each SOUL.md) must be 15 KB or less."""
    problems = []
    for e in entries:
        if e["status"] == "D" or not is_skill_file(e["path"]):
            continue
        size = gitops.blob_size(repo, rev, e["path"])
        if size is not None and size > limit:
            problems.append("%s is %d bytes. The limit is %d." % (e["path"], size, limit))
    return problems
