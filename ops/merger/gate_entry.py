"""Runs INSIDE the throwaway gate container. The merger copies this file in from the trusted live repo.

Mounts:   /src (candidate tree, read-only)   /hidden (hidden tests, read-only, only for this run)
          /gate (this file and job.json)     /rules (semgrep rules)    /osv (vulnerability databases)
Network:  none.   Root file system: read-only.   User: 65534.   Writable: /tmp only.

Output: one line that starts with GATE_RESULT and holds JSON. The merger reads it. The details stay with root.
Agents learn only pass or fail.

Two phases, each in its own container. The merger starts them. See D-074.
  trusted   G1 (the fleet tests and the hidden tests), G2, and G4. No file that an agent may change runs here.
            The tests, the scanners, and the code under test all come from paths that gate G3 protects.
  projects  The tests of each changed project. An agent writes these tests, so this is agent code. It runs last,
            in a second container that has no hidden tests. The merger reads only the key "P" from this phase.
            A process that forges a result line there can change nothing but the verdict on its own tests.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

PY = "/opt/test/bin/python"
SRC = pathlib.Path("/src")
HIDDEN = pathlib.Path("/hidden")
WORK = pathlib.Path("/tmp/work")
CODE_EXT = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".sh", ".bash"}
LOCKFILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Pipfile.lock", "uv.lock", "go.mod", "Cargo.lock",
             "requirements.txt", "pdm.lock", "composer.lock", "Gemfile.lock"}


def run(cmd, cwd, extra_env=None, timeout=900):
    # Each command is a fixed list in this file. No shell runs. The gate container has no network and no secrets.
    env = {"PATH": "/opt/test/bin:/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
           "LITELLM_LOCAL_MODEL_COST_MAP": "True", "FLEET_CANDIDATE": str(WORK / "src")}
    env.update(extra_env or {})
    try:
        proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)  # nosemgrep: dangerous-subprocess-use-audit
        return proc.returncode, (proc.stdout + proc.stderr)
    except subprocess.TimeoutExpired:
        return 124, "timeout after %d seconds" % timeout
    except OSError as exc:
        return 127, "cannot run %s: %s" % (cmd[0], exc)


def tail(text, n=900):
    return text[-n:]


def copy_source():
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    shutil.copytree(SRC, WORK / "src", symlinks=True, ignore=shutil.ignore_patterns("__pycache__", ".git"))


def has_tests(folder):
    for pattern in ("test_*.py", "*_test.py"):
        if any(folder.rglob(pattern)):
            return "pytest"
    for pattern in ("*.test.js", "*.test.mjs", "*.spec.js", "*.test.ts"):
        if any(p for p in folder.rglob(pattern) if "node_modules" not in p.parts):
            return "node"
    return None


def project_dirs(changed):
    seen = []
    for path in changed:
        parts = path.split("/")
        if len(parts) > 2 and parts[0] == "projects" and not parts[1].startswith("_") and parts[1] not in seen:
            seen.append(parts[1])
    return seen


def gate_g1(job):
    """The fleet tests, the policy tests, and the hidden tests. The tests of a changed project run in the second phase."""
    notes, ok = [], True
    src = WORK / "src"
    rc, out = run([PY, "-m", "pytest", "-p", "no:cacheprovider", "-q", "-m", "not deployed", "core/tests/policy", "tests/unit", "tests/injection"],
                  src, timeout=1200)
    notes.append("fleet tests rc=%d %s" % (rc, tail(out, 300)))
    ok &= rc == 0
    if HIDDEN.is_dir() and any(HIDDEN.glob("test_*.py")):
        rc, out = run([PY, "-m", "pytest", "-p", "no:cacheprovider", "-q", "--rootdir=/hidden", "-c", "/dev/null", str(HIDDEN)], src, timeout=600)
        notes.append("hidden tests rc=%d %s" % (rc, tail(out, 300)))
        ok &= rc == 0
    else:
        notes.append("hidden tests: none found")
        ok = False
    return ok, "; ".join(notes)


def gate_projects(job):
    """The tests of each changed project. Agent code. It runs alone, in the second container."""
    notes, ok = [], True
    src = WORK / "src"
    for slug in project_dirs(job.get("changed", [])):
        folder = src / "projects" / slug
        kind = has_tests(folder) if folder.is_dir() else None
        if kind == "pytest":
            rc, out = run([PY, "-m", "pytest", "-p", "no:cacheprovider", "-q", "-c", "/dev/null", "--rootdir=%s" % folder, str(folder)], folder, timeout=600)
        elif kind == "node":
            rc, out = run(["node", "--test"], folder, timeout=600)
        else:
            continue
        notes.append("project %s (%s) rc=%d %s" % (slug, kind, rc, tail(out, 200)))
        ok &= rc == 0
    return ok, "; ".join(notes)


def gate_g4(job):
    """The Rule of Two test, run on its own so that its result is reported alone."""
    rc, out = run([PY, "-m", "pytest", "-p", "no:cacheprovider", "-q", "-m", "not deployed", "core/tests/policy/test_rule_of_two.py"], WORK / "src", timeout=300)
    return rc == 0, "rc=%d %s" % (rc, tail(out, 300))


def scan_gitleaks(job):
    # Scan from the tree root with a relative path. The allowlist in .gitleaks.toml matches relative paths.
    src = WORK / "src"
    cmd = ["gitleaks", "dir", ".", "--no-banner", "--redact", "--exit-code", "1"]
    if (src / ".gitleaks.toml").exists():
        cmd += ["--config", ".gitleaks.toml"]
    rc, out = run(cmd, src, timeout=300)
    return rc == 0, "gitleaks rc=%d %s" % (rc, tail(out, 200))


def scan_semgrep(job):
    files = [str(WORK / "src" / p) for p in job.get("changed", []) if pathlib.Path(p).suffix in CODE_EXT and (WORK / "src" / p).is_file()]
    if not files:
        return True, "semgrep: no changed code files"
    rc, out = run(["semgrep", "scan", "--config", "/rules", "--metrics=off", "--disable-version-check", "--error", "--severity", "ERROR",
                   "--quiet", "--json", "--timeout", "30"] + files, WORK, extra_env={"SEMGREP_SEND_METRICS": "off"}, timeout=600)
    try:
        data = json.loads(out[out.index("{"):])
    except ValueError:
        return False, "semgrep gave no result: " + tail(out, 200)
    found = ["%s:%s" % (r["check_id"].split(".")[-1], r["path"].replace(str(WORK / "src"), "")) for r in data.get("results", [])]
    return not found, "semgrep findings: %s" % (", ".join(found[:8]) or "none")


def scan_osv(job):
    manifests = [str(WORK / "src" / p) for p in job.get("changed", []) if pathlib.Path(p).name in LOCKFILES and (WORK / "src" / p).is_file()
                 or (pathlib.Path(p).name.startswith("requirements") and p.endswith(".txt") and (WORK / "src" / p).is_file())]
    if not manifests:
        return True, "osv: no changed dependency files"
    if not any(pathlib.Path("/osv").rglob("all.zip")):
        return False, "osv: the vulnerability databases are missing. Fail closed."
    cmd = ["osv-scanner", "scan", "source", "--offline-vulnerabilities", "--no-resolve", "--format", "json"]
    for m in manifests:
        cmd += ["-L", m]
    rc, out = run(cmd, WORK, extra_env={"OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY": "/osv"}, timeout=300)
    try:
        data = json.loads(out[out.index("{"):])
    except ValueError:
        return rc == 0, "osv rc=%d %s" % (rc, tail(out, 200))
    vulns = [(p["package"]["name"], len(p.get("vulnerabilities", []))) for r in data.get("results", []) for p in r.get("packages", []) if p.get("vulnerabilities")]
    return not vulns, "osv vulnerable packages: %s" % (vulns[:6] or "none")


def skill_dirs(changed):
    found = []
    for path in changed:
        if not path.startswith("skills/"):
            continue
        d = (WORK / "src" / path).parent
        while d != WORK / "src" and not (d / "SKILL.md").exists() and d.parent != d:
            d = d.parent
        if (d / "SKILL.md").exists() and d not in found:
            found.append(d)
    return found


def scan_skills(job):
    dirs = skill_dirs(job.get("changed", []))
    if not dirs:
        return True, "skill-scanner: no changed skills"
    bad = []
    for d in dirs:
        rc, out = run(["skill-scanner", "scan", str(d), "--policy", "balanced", "--fail-on-severity", "high", "--format", "summary"], WORK, timeout=300)
        if rc != 0:
            bad.append("%s rc=%d" % (d.name, rc))
    return not bad, "skill-scanner: %d skill(s), failed: %s" % (len(dirs), ", ".join(bad) or "none")


def scan_mcp(job):
    """Scan the descriptions of the fleet's own MCP tools with the Cisco mcp-scanner (static, YARA, offline)."""
    code = ("import json,sys; sys.path.insert(0, %r)\n"
            "from ops.mcp import tools\n"
            "print(json.dumps({'tools':[{'name':n,'description':t['description'],'inputSchema':t['schema']} for n,t in tools.TOOLS.items()]}))\n") % str(WORK / "src")
    rc, out = run([PY, "-c", code], WORK, timeout=60)
    if rc != 0:
        return False, "could not list the MCP tools: " + tail(out, 200)
    tools_json = WORK / "tools.json"
    tools_json.write_text(out[out.index("{"):])
    rc, out = run(["mcp-scanner", "--analyzers", "yara", "--format", "raw", "static", "--tools", str(tools_json)], WORK, timeout=300)
    try:
        data = json.loads(out[out.index("{"):])
    except ValueError:
        return False, "mcp-scanner gave no result: " + tail(out, 200)
    unsafe = [r.get("tool_name") for r in data.get("scan_results", []) if not r.get("is_safe", False)]
    return not unsafe, "mcp-scanner: %d tool(s), unsafe: %s" % (len(data.get("scan_results", [])), ", ".join(map(str, unsafe)) or "none")


def gate_g2(job):
    notes, ok = [], True
    for fn in (scan_gitleaks, scan_semgrep, scan_osv, scan_skills, scan_mcp):
        passed, note = fn(job)
        notes.append(("PASS " if passed else "FAIL ") + note)
        ok &= passed
    return ok, "; ".join(notes)


def main(argv):
    job = json.loads(pathlib.Path(argv[1] if len(argv) > 1 else "/gate/job.json").read_text())
    wanted = job.get("gates", ["G1", "G2", "G4"])
    started = time.time()
    copy_source()
    result = {}
    if job.get("phase") == "projects":
        ok, detail = gate_projects(job)
        result["P"] = {"ok": bool(ok), "detail": (detail or "no project tests")[:2500]}
    else:
        for name, fn in (("G1", gate_g1), ("G2", gate_g2), ("G4", gate_g4)):
            if name in wanted:
                ok, detail = fn(job)
                result[name] = {"ok": bool(ok), "detail": detail[:2500]}
    result["seconds"] = round(time.time() - started)
    print("GATE_RESULT " + json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
