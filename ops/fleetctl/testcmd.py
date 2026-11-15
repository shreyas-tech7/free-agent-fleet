"""fleetctl test: policy tests, unit tests, and injection tests, each in its own pytest run.

A fourth stage, gate, collects the three folders in one session without running them. Gate G1 of the merger runs them in one session.
A name that two conftest.py files or two test files share breaks only that session. See D-056.
"""
import os
import re
import subprocess
import sys

from ops.fleetlib import paths

SUITES = [("policy", "core/tests/policy"), ("unit", "tests/unit"), ("injection", "tests/injection")]
GATE_FOLDERS = [folder for _name, folder in SUITES]      # the same folders, in the same order, as the G1 command of the merger


def _env():
    keep = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "USER")}
    keep.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    keep["PYTHONDONTWRITEBYTECODE"] = "1"
    keep["PYTHONHASHSEED"] = "0"
    return keep


def _counts(text):
    result = {}
    for label in ("passed", "failed", "skipped", "error", "errors"):
        m = re.search(r"(\d+) %s" % label, text)
        if m:
            result[label] = int(m.group(1))
    return result


def run_suite(root, folder, extra, runner=subprocess.run):
    # pytest.ini also sets -q. A second -q hides the summary line. So the options are set once, here.
    argv = [sys.executable, "-B", "-m", "pytest", folder, "--no-header", "-o", "addopts=-p no:cacheprovider -q"] + extra
    proc = runner(argv, cwd=str(root), env=_env(), capture_output=True, text=True, timeout=3600)
    return proc.returncode, proc.stdout + proc.stderr


def run_gate_collect(root, runner=subprocess.run):
    """Collect the tests of every folder in one pytest session, as gate G1 does. Nothing runs."""
    argv = [sys.executable, "-B", "-m", "pytest", "--collect-only", "--no-header", "-o", "addopts=-p no:cacheprovider -q", "-m", "not deployed"] + GATE_FOLDERS
    proc = runner(argv, cwd=str(root), env=_env(), capture_output=True, text=True, timeout=900)
    return proc.returncode, proc.stdout + proc.stderr


def cmd_test(args, console, runner=subprocess.run):
    root = paths.repo_root()
    extra = []
    if args.k:
        extra += ["-k", args.k]
    if args.x:
        extra += ["-x"]
    wanted = args.suite or [name for name, _f in SUITES]
    failed = []
    for name, folder in SUITES:
        if name not in wanted:
            continue
        if not (root / folder).is_dir():
            console.say("%-10s MISSING (%s)" % (name, folder))
            failed.append(name)
            continue
        rc, out = run_suite(root, folder, extra, runner)
        counts = _counts(out)
        if rc == 5:                                   # pytest found no test: with -k that is fine
            console.say("%-10s no test matched" % name)
            continue
        line = ", ".join("%d %s" % (n, k) for k, n in counts.items()) or "no summary"
        console.say("%-10s %s (%s)" % (name, "PASS" if rc == 0 else "FAIL", line))
        if rc != 0:
            failed.append(name)
            console.say(out[-3000:])
    if not args.suite or "gate" in args.suite:
        rc, out = run_gate_collect(root, runner)
        found = re.search(r"(\d+) tests? collected", out)
        console.say("%-10s %s (%s)" % ("gate", "PASS" if rc == 0 else "FAIL", "%s collected in one session" % found.group(1) if found and rc == 0 else "collection error"))
        if rc != 0:
            failed.append("gate")
            console.say(out[-3000:])
    console.say("")
    console.say("fleetctl test: %s" % ("PASS" if not failed else "FAIL (%s)" % ", ".join(failed)))
    return 0 if not failed else 1
