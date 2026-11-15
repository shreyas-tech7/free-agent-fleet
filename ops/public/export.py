"""fleetctl public export. Build the public copy of the repository. See D-051.

Steps:
 1. Read the allowlist public/manifest.txt. Take only files that git tracks. Hard deny rules beat the allowlist.
 2. Copy public/overlay/ on top. The overlay holds the files that exist only in the public repository (the README, the license, and so on).
 3. Check that each private owner file has its template in the tree (public/transforms.yaml).
 4. Run the scans: the deny list, generic patterns, gitleaks, and OSV-Scanner. Any finding fails the export, and nothing is written.
 5. Write docs/PUBLIC_SCRUB_REPORT.md in the private repository. It names files and rules and never shows text.
 6. Make the public repository: one first commit with no history, or a branch for a pull request.
"""
import datetime
import fnmatch
import os
import pathlib
import shutil
import subprocess
import tempfile

import yaml

from ops.public import scans

# Paths that never leave the private repository, even when the manifest lists them.
HARD_DENY = (
    "config/owner.yaml", ".env", "*/.env", ".env.*", "*/.env.*", "*/secrets/*", "secrets/*", "*.pem", "*.key",
    "projects/*", "*/MEMORY.md", "MEMORY.md", "*/USER.md", "USER.md", "*.sqlite", "*.sqlite3", "*.db", "*.log", "*/logs/*", "logs/*",
    "*/backups/*", "backups/*", "*/ledger/*", "HANDOFF.md", "BUILD_STATE.md", "NOTICE", "docs/BLOCKED.md", "docs/DECISIONS.md",
    "docs/SUDO_LOG.md", "docs/PORTFOLIO_LOG.md", "docs/PUBLIC_SCRUB_REPORT.md", ".git/*", "*/__pycache__/*", "*.pyc",
)
HARD_ALLOW = (".env.example", "*/.env.example", "n8n/.env.example", "projects/README.md", "projects/_studio/README.md", "projects/_graveyard/README.md")   # placeholders only
IDENTITY_NOREPLY = "%d+%s@users.noreply.github.com"


class ExportError(Exception):
    pass


def _hit(rel, pattern):
    if pattern.endswith("/"):
        return rel.startswith(pattern)
    return rel == pattern or fnmatch.fnmatch(rel, pattern)


def read_manifest(path):
    include, exclude = [], []
    try:
        lines = pathlib.Path(path).read_text().splitlines()
    except OSError:
        raise ExportError("the manifest %s cannot be read" % path) from None
    for line in lines:
        line = line.split("  #")[0].strip()
        if not line or line.startswith("#"):
            continue
        (exclude if line.startswith("!") else include).append(line.lstrip("!"))
    if not include:
        raise ExportError("the manifest lists no path")
    return include, exclude


def denied(rel):
    if any(_hit(rel, p) for p in HARD_ALLOW):
        return False
    return any(_hit(rel, p) for p in HARD_DENY)


def selected(rel, include, exclude):
    return any(_hit(rel, p) for p in include) and not any(_hit(rel, p) for p in exclude) and not denied(rel)


def git(repo, *args, runner=subprocess.run, env=None, check=True):
    proc = runner(["git", "-C", str(repo), "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"] + list(args),  # nosemgrep: dangerous-subprocess-use-audit
                  capture_output=True, text=True, timeout=300, env=env)
    if check and proc.returncode != 0:
        raise ExportError("git %s failed: %s" % (args[0], (proc.stderr or proc.stdout).strip()[-200:]))
    return proc.stdout


def tracked_files(repo, runner=subprocess.run):
    out = git(repo, "ls-files", "-z", runner=runner)
    return sorted(p for p in out.split("\0") if p)


def is_clean(repo, runner=subprocess.run):
    return not git(repo, "status", "--porcelain", "--untracked-files=no", runner=runner).strip()


def build_tree(repo, out, manifest, overlay=None, runner=subprocess.run):
    """Copy the allowed files into `out`. Returns the sorted list of relative paths that are in the tree."""
    repo, out = pathlib.Path(repo), pathlib.Path(out)
    include, exclude = read_manifest(manifest)
    copied = []
    for rel in tracked_files(repo, runner):
        if not selected(rel, include, exclude):
            continue
        source = repo / rel
        if not source.is_file() or source.is_symlink():
            continue
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(source), str(target))
        copied.append(rel)
    overlay = pathlib.Path(overlay) if overlay else None
    if overlay and overlay.is_dir():
        for source in sorted(p for p in overlay.rglob("*") if p.is_file()):
            rel = str(source.relative_to(overlay))
            if denied(rel):
                raise ExportError("the overlay file %s is on the hard deny list" % rel)
            target = out / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(source), str(target))
            copied.append(rel)
    return sorted(set(copied))


def check_templates(tree, transforms):
    """Each private owner file must have its template in the public tree, and the template must be valid. Returns a list of problems."""
    problems = []
    try:
        entries = (yaml.safe_load(pathlib.Path(transforms).read_text()) or {}).get("templates", [])
    except OSError:
        return ["the transforms file %s cannot be read" % transforms]
    for entry in entries:
        template = pathlib.Path(tree) / entry["template"]
        if not template.is_file():
            problems.append("the template %s is missing" % entry["template"])
            continue
        if entry.get("validator") == "owner":
            from ops.fleetlib import owner
            bad = owner.validate(owner.read(template))
            problems += ["the template %s: %s" % (entry["template"], b) for b in bad]
        if entry["private"] not in HARD_DENY and not denied(entry["private"]):
            problems.append("the private file %s is not on the hard deny list" % entry["private"])
    return problems


def run_scans(tree, terms_file, allow_file, runner=subprocess.run, with_osv=True):
    """Return {"deny-list and patterns": [...], "gitleaks": [...], "osv": [...]}. Each value is a list of (path, line, rule)."""
    terms = scans.load_terms(terms_file)
    allow = scans.load_allow(allow_file)
    results = {"patterns": scans.scan_tree(tree, terms, allow), "gitleaks": scans.run_gitleaks(tree, runner=runner)}
    results["osv"] = scans.run_osv(tree, runner=runner) if with_osv else []
    return results, len(terms)


def write_report(path, commit, files, results, term_count, templates_problems, now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    findings = [f for rows in results.values() for f in rows]
    ok = not findings and not templates_problems
    lines = ["# Public scrub report", "",
             "`fleetctl public export` wrote this file. It names files and rules. It never shows the matched text.", "",
             "- Date: %s" % now.strftime("%Y-%m-%d %H:%M UTC"),
             "- Private commit: %s" % commit[:10],
             "- Files in the export: %d" % len(files),
             "- Result: **%s**" % ("PASS" if ok else "FAIL"), "",
             "| Scan | Result | Findings |", "|---|---|---|",
             "| Deny list (%d terms) and generic patterns | %s | %d |" % (term_count, "pass" if not results["patterns"] else "FAIL", len(results["patterns"])),
             "| gitleaks (no git history) | %s | %d |" % ("pass" if not results["gitleaks"] else "FAIL", len(results["gitleaks"])),
             "| OSV-Scanner | %s | %d |" % ("pass" if not results["osv"] else "FAIL", len(results["osv"])),
             "| Templates for owner files | %s | %d |" % ("pass" if not templates_problems else "FAIL", len(templates_problems)), ""]
    if findings or templates_problems:
        lines += ["## Findings", ""]
        for name, rows in results.items():
            for rel, line, rule in rows[:200]:
                lines.append("- %s: `%s%s` %s" % (name, rel, ":%d" % line if line else "", rule))
        lines += ["- templates: %s" % p for p in templates_problems]
        lines.append("")
    summary = scans.summarize(findings)
    if summary:
        lines += ["Counts by rule: " + ", ".join("%s %d" % kv for kv in summary.items()) + ".", ""]
    pathlib.Path(path).write_text("\n".join(lines))
    return ok


def github_identity(runner=subprocess.run):
    """The GitHub login and the noreply address, from `gh api user`. The address has no real email."""
    proc = runner(["gh", "api", "user", "--jq", ".login + \" \" + (.id|tostring)"], capture_output=True, text=True, timeout=60)  # nosemgrep: dangerous-subprocess-use-audit
    parts = proc.stdout.split()
    if proc.returncode != 0 or len(parts) != 2 or not parts[1].isdigit():
        raise ExportError("could not read the GitHub login. Run: gh auth login")
    return parts[0], IDENTITY_NOREPLY % (int(parts[1]), parts[0])


def sync_public(tree, public_repo, login, noreply, message="Initial commit", date=None, runner=subprocess.run):
    """Make the public repository. A new one gets one commit with no history. An existing one gets a branch for a pull request."""
    public_repo = pathlib.Path(public_repo)
    env = dict(os.environ, GIT_AUTHOR_NAME=login, GIT_AUTHOR_EMAIL=noreply, GIT_COMMITTER_NAME=login, GIT_COMMITTER_EMAIL=noreply,
               GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
    fresh = not (public_repo / ".git").exists()
    if fresh:
        public_repo.mkdir(parents=True, exist_ok=True)
        git(public_repo, "init", "-q", "-b", "main", runner=runner, env=env)
        branch = "main"
    else:
        if git(public_repo, "status", "--porcelain", runner=runner, env=env).strip():
            raise ExportError("the public repository has uncommitted changes. Commit or discard them first.")
        date = date or datetime.date.today().isoformat()
        branch = "sync/%s" % date
        git(public_repo, "checkout", "-q", "main", runner=runner, env=env)
        git(public_repo, "checkout", "-q", "-B", branch, runner=runner, env=env)
        message = "Sync from the private repository (%s)" % date
    for entry in public_repo.iterdir():
        if entry.name != ".git":
            shutil.rmtree(str(entry)) if entry.is_dir() and not entry.is_symlink() else entry.unlink()
    for source in sorted(pathlib.Path(tree).rglob("*")):
        rel = source.relative_to(tree)
        target = public_repo / rel
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(source), str(target))
    git(public_repo, "add", "-A", runner=runner, env=env)
    if not git(public_repo, "status", "--porcelain", runner=runner, env=env).strip():
        if not fresh:                                  # nothing changed. Leave no empty branch behind.
            git(public_repo, "checkout", "-q", "main", runner=runner, env=env)
            git(public_repo, "branch", "-q", "-D", branch, runner=runner, env=env)
            branch = "main"
        return {"branch": branch, "committed": False, "fresh": fresh}
    git(public_repo, "commit", "-q", "-m", message, runner=runner, env=env)
    return {"branch": branch, "committed": True, "fresh": fresh, "commit": git(public_repo, "rev-parse", "HEAD", runner=runner, env=env).strip()}


def export(repo, public_repo, terms_file, report_path, manifest=None, overlay=None, transforms=None, allow_file=None,
           runner=subprocess.run, with_osv=True, identity=None, allow_dirty=False, write_public=True):
    """Run the whole export. Returns a result dictionary. It raises ExportError when it cannot run. A failed scan is a result, not an error."""
    repo = pathlib.Path(repo)
    manifest = manifest or repo / "public" / "manifest.txt"
    overlay = overlay or repo / "public" / "overlay"
    transforms = transforms or repo / "public" / "transforms.yaml"
    allow_file = allow_file or repo / "public" / "scan-allow.txt"
    if not allow_dirty and not is_clean(repo, runner):
        raise ExportError("the private repository has uncommitted changes. Commit first, so the report names a commit.")
    commit = git(repo, "rev-parse", "HEAD", runner=runner).strip()
    with tempfile.TemporaryDirectory(prefix="public-export-") as tmp:
        tree = pathlib.Path(tmp) / "tree"
        tree.mkdir()
        files = build_tree(repo, tree, manifest, overlay, runner)
        problems = check_templates(tree, transforms)
        results, term_count = run_scans(tree, terms_file, allow_file, runner, with_osv)
        ok = write_report(report_path, commit, files, results, term_count, problems)
        result = {"ok": ok, "files": len(files), "commit": commit, "findings": scans.summarize([f for rows in results.values() for f in rows]),
                  "template_problems": problems}
        if ok and write_public:
            login, noreply = identity or github_identity(runner)
            result["public"] = sync_public(tree, public_repo, login, noreply, runner=runner)
        return result
