"""Policy: locked core files have root ownership and safe modes on the installed system."""
import fnmatch
import pathlib
import stat

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
PROTECTED = ROOT / "core" / "policy" / "protected_paths.txt"
LIVE = pathlib.Path("/opt/agent-fleet/repo")


def patterns():
    out = []
    for line in PROTECTED.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def covered(path, pats):
    """True when a protected pattern matches the path. A pattern that ends in / matches everything below it."""
    for pat in pats:
        if pat.endswith("/") and path.startswith(pat):
            return True
        if fnmatch.fnmatch(path, pat):
            return True
    return False


def test_protected_list_covers_the_locked_core():
    pats = patterns()
    for need in ("core/RULES.md", "core/killswitch/killswitch.py", "core/tests/hidden/x.py", "config/hermes.managed.yaml",
                 "ops/merger/runner.py", "ops/control/main.py", "ops/publisher/server.py", "ops/deployer/core.py",
                 "fleetctl", "install/60-units.sh", "systemd/agent-fleet.target", "agents/build/agent.yaml",
                 "conftest.py", "tests/unit/test_x.py", "skills/x/conftest.py", "projects/p/mcp.json"):
        assert covered(need, pats), need


def test_agent_writable_list_never_overlaps_the_protected_list():
    writable = [l.strip() for l in (ROOT / "core" / "policy" / "agent_writable_paths.txt").read_text().splitlines()
                if l.strip() and not l.startswith("#")]
    assert writable
    pats = patterns()
    samples = {"agents/*/SOUL.md": "agents/build/SOUL.md", "skills/": "skills/x/SKILL.md", "projects/": "projects/p/app/index.js",
               "docs/": "docs/NOTES.md", "config/stages.yaml": "config/stages.yaml"}
    for w in writable:
        assert w in samples, "add a sample for %s" % w
        assert not covered(samples[w], pats), "%s is protected and writable" % w


def expand(base):
    files = []
    for pat in patterns():
        if pat.endswith("/"):
            root = base / pat
            if root.is_dir():
                files += [p for p in root.rglob("*")]
                files.append(root)
        else:
            files += list(base.glob(pat))
    return sorted(set(files))


def test_live_locked_core_is_root_owned_and_not_writable(live_repo):
    files = expand(live_repo)
    assert files, "no locked core files found in the live copy"
    bad = []
    for p in files:
        st = p.lstat()
        mode = stat.S_IMODE(st.st_mode)
        if st.st_uid != 0 or (mode & 0o022):
            bad.append("%s owner=%d mode=%o" % (p, st.st_uid, mode))
    assert not bad, bad[:10]


def test_live_repo_itself_is_root_owned(live_repo):
    st = live_repo.stat()
    assert st.st_uid == 0 and not (stat.S_IMODE(st.st_mode) & 0o022)


def test_hidden_tests_folder_is_private(live_repo):
    hidden = pathlib.Path("/opt/agent-fleet/core/tests/hidden")
    if not hidden.exists():
        pytest.skip("hidden tests are not installed yet")
    try:
        st = hidden.stat()
    except PermissionError:
        pytest.skip("cannot see the hidden folder. That is the correct state for a normal user.")
    assert st.st_uid == 0 and stat.S_IMODE(st.st_mode) == 0o700


def test_hermes_hooks_folder_is_root_owned(live_repo):
    hooks = pathlib.Path("/home/fleet/.hermes/hooks")
    try:
        exists = hooks.exists()
    except PermissionError:
        pytest.skip("home of the fleet user is private")
    if not exists:
        pytest.skip("Hermes is not installed yet")
    st = hooks.stat()
    assert st.st_uid == 0 and stat.S_IMODE(st.st_mode) == 0o755
