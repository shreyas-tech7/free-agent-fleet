"""The public export. Planted values are built at run time, so this file holds no real address, token, or private name. See D-051."""
import json
import os
import pathlib
import subprocess

import pytest
import yaml

from ops.public import export, scans

PRIV_TERM = "Zorblax Quantum"
DOT = "."
IP = DOT.join(["192", "168", "7", "7"])
ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com",
           GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")


def sh(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd)] + list(args), capture_output=True, text=True, check=True, env=ENV).stdout.strip()  # nosemgrep: dangerous-subprocess-use-audit


def write(root, rel, text="x\n"):
    path = pathlib.Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def private(tmp_path):
    repo = tmp_path / "private"
    repo.mkdir()
    sh(repo, "init", "-q", "-b", "main")
    files = {
        "ops/tool.py": "print('hello')\n", "core/RULES.md": "rules\n", "install.sh": "#!/bin/sh\n", "README.md": "private readme " + PRIV_TERM + "\n",
        "config/owner.yaml": "owner: {name: Z}\n", "config/fleet.example.yaml": "owner: {name: Your Name}\n",
        "docs/DECISIONS.md": "private " + PRIV_TERM + "\n", "docs/RUNBOOK.md": "pause and stop\n", "HANDOFF.md": "x\n", "BUILD_STATE.md": "x\n", "NOTICE": "n\n",
        "projects/secret-project/STATE.md": "x\n", "agents/a/MEMORY.md": "memory\n", "scripts/slog": "x\n", "scripts/ok.sh": "x\n",
        ".env": "TOKEN=x\n", ".env.example": "TOKEN=\n", "tests/test_a.py": "def test_a():\n    pass\n",
        "templates/owner.example.yaml": yaml.safe_dump({"owner": {"name": "Your Name", "email": "you@example.com", "timezone": "America/Chicago"},
                                                        "brief": {"time": "07:00", "max_words": 150, "channels": ["slack"]}, "quiet_hours": {"start": "22:00", "end": "07:00"},
                                                        "channels": {"main": "slack", "backups": ["telegram"]}}),
    }
    for rel, text in files.items():
        write(repo, rel, text)
    write(repo, "untracked.txt", "never tracked\n")
    sh(repo, "add", "-f", *files)
    sh(repo, "commit", "-q", "-m", "init")
    manifest = write(repo / "public", "manifest.txt", "\n".join(["ops/", "core/", "agents/", "install.sh", "config/", "docs/", "templates/", "tests/", "scripts/", "projects/", ".env.example", "HANDOFF.md",
                                                                  "README.md", "NOTICE", "BUILD_STATE.md", ".env", "!scripts/slog", ""]))
    write(repo / "public", "transforms.yaml", yaml.safe_dump({"templates": [{"private": "config/owner.yaml", "template": "templates/owner.example.yaml", "validator": "owner"}]}))
    write(repo / "public", "scan-allow.txt", "# none\n")
    write(repo / "public" / "overlay", "README.md", "# public readme\n")
    write(repo / "public" / "overlay", "LICENSE", "MIT\n")
    sh(repo, "add", "-f", "public")
    sh(repo, "commit", "-q", "-m", "public files")
    return repo


class Done:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def fake_runner(gitleaks=(), osv=None):
    """Runs git for real. Answers gitleaks, osv-scanner, and gh with canned results."""
    def runner(cmd, **kw):
        if cmd[0] == "gitleaks":
            report = pathlib.Path(cmd[cmd.index("--report-path") + 1])
            report.write_text(json.dumps(list(gitleaks)))
            return Done(1 if gitleaks else 0)
        if cmd[0] == "osv-scanner":
            return Done(1 if osv else 0, json.dumps({"results": osv or []}))
        if cmd[0] == "gh":
            return Done(0, "octo 4242\n")
        return subprocess.run(cmd, **dict(kw, env=kw.get("env") or ENV))  # nosemgrep: dangerous-subprocess-use-audit
    return runner


@pytest.fixture
def terms(tmp_path):
    path = tmp_path / "terms.txt"
    path.write_text("# private\n" + PRIV_TERM + "\n")
    path.chmod(0o600)
    return path


def test_the_hard_deny_list_beats_the_manifest():
    for rel in ("config/owner.yaml", ".env", "x/.env", "projects/p/STATE.md", "agents/a/MEMORY.md", "a/USER.md", "HANDOFF.md", "BUILD_STATE.md", "NOTICE",
                "docs/DECISIONS.md", "docs/SUDO_LOG.md", "data/x.sqlite", "a.db", "n8n/logs/x", "backups/b", "docs/PUBLIC_SCRUB_REPORT.md", ".git/config"):
        assert export.denied(rel), rel
    for rel in (".env.example", "n8n/.env.example", "projects/README.md", "projects/_studio/README.md", "ops/tool.py", "docs/RUNBOOK.md"):
        assert not export.denied(rel), rel


def test_the_manifest_reads_includes_excludes_and_comments(tmp_path):
    path = write(tmp_path, "m.txt", "# c\nops/\n\n!ops/private.py\nREADME.md  # why\n")
    assert export.read_manifest(path) == (["ops/", "README.md"], ["ops/private.py"])
    with pytest.raises(export.ExportError):
        export.read_manifest(write(tmp_path, "empty.txt", "# nothing\n"))
    with pytest.raises(export.ExportError):
        export.read_manifest(tmp_path / "missing.txt")


def test_the_tree_holds_only_tracked_allowed_files_plus_the_overlay(private, tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    files = export.build_tree(private, tree, private / "public" / "manifest.txt", private / "public" / "overlay", runner=fake_runner())
    assert "ops/tool.py" in files and "install.sh" in files and ".env.example" in files and "scripts/ok.sh" in files
    assert "README.md" in files and (tree / "README.md").read_text() == "# public readme\n"          # the overlay replaced the private README
    for rel in ("config/owner.yaml", "docs/DECISIONS.md", "HANDOFF.md", "BUILD_STATE.md", "NOTICE", ".env", "agents/a/MEMORY.md", "scripts/slog", "untracked.txt",
                "projects/secret-project/STATE.md"):
        assert rel not in files and not (tree / rel).exists(), rel
    assert (tree / "LICENSE").exists()


def test_an_overlay_file_on_the_hard_deny_list_is_refused(private, tmp_path):
    write(private / "public" / "overlay", "NOTICE", "x\n")
    tree = tmp_path / "tree"
    tree.mkdir()
    with pytest.raises(export.ExportError):
        export.build_tree(private, tree, private / "public" / "manifest.txt", private / "public" / "overlay", runner=fake_runner())


def test_each_owner_file_needs_a_valid_template_in_the_tree(private, tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    export.build_tree(private, tree, private / "public" / "manifest.txt", private / "public" / "overlay", runner=fake_runner())
    assert export.check_templates(tree, private / "public" / "transforms.yaml") == []
    (tree / "templates" / "owner.example.yaml").write_text("owner: {name: ''}\n")
    assert any("owner.name is empty" in p for p in export.check_templates(tree, private / "public" / "transforms.yaml"))
    (tree / "templates" / "owner.example.yaml").unlink()
    assert export.check_templates(tree, private / "public" / "transforms.yaml") == ["the template templates/owner.example.yaml is missing"]


def test_a_clean_export_writes_a_one_commit_public_repository_with_the_noreply_identity(private, tmp_path, terms):
    public = tmp_path / "public"
    report = tmp_path / "report.md"
    result = export.export(private, public, terms, report, runner=fake_runner(), with_osv=True)
    assert result["ok"] is True and result["public"]["fresh"] and result["public"]["committed"]
    assert sh(public, "rev-list", "--count", "HEAD") == "1" and sh(public, "log", "-1", "--format=%an <%ae>") == "octo <4242+octo@users.noreply.github.com>"
    assert sh(public, "log", "-1", "--format=%s") == "Initial commit"
    assert (public / "LICENSE").exists() and not (public / "docs" / "DECISIONS.md").exists() and not (public / ".env").exists()
    text = report.read_text()
    assert "**PASS**" in text and "| gitleaks (no git history) | pass | 0 |" in text and PRIV_TERM not in text


def test_a_deny_list_hit_fails_the_export_and_writes_nothing_to_the_public_repository(private, tmp_path, terms):
    write(private, "docs/RUNBOOK.md", "contact " + PRIV_TERM + " now\n")
    sh(private, "add", "-A")
    sh(private, "commit", "-q", "-m", "oops")
    public = tmp_path / "public"
    report = tmp_path / "report.md"
    result = export.export(private, public, terms, report, runner=fake_runner())
    assert result["ok"] is False and result["findings"] == {"deny-list": 1} and "public" not in result and not public.exists()
    text = report.read_text()
    assert "**FAIL**" in text and "docs/RUNBOOK.md:1" in text and "deny-list" in text and PRIV_TERM not in text


def test_gitleaks_osv_and_pattern_findings_each_fail_the_export(private, tmp_path, terms):
    write(private, "ops/tool.py", "host = '" + IP + "'\n")
    sh(private, "add", "-A")
    sh(private, "commit", "-q", "-m", "ip")
    leak = {"File": "x", "StartLine": 3, "RuleID": "generic-api-key"}
    osv = [{"source": {"path": "package-lock.json"}, "packages": [{"package": {"name": "lodash"}, "vulnerabilities": [{"id": "X"}]}]}]
    result = export.export(private, tmp_path / "pub", terms, tmp_path / "r.md", runner=fake_runner(gitleaks=[leak], osv=osv))
    assert result["ok"] is False and set(result["findings"]) == {"private-ip", "gitleaks:generic-api-key", "osv:lodash"}


def test_a_dirty_private_repository_is_refused_unless_allowed(private, tmp_path, terms):
    write(private, "ops/tool.py", "changed\n")
    with pytest.raises(export.ExportError):
        export.export(private, tmp_path / "pub", terms, tmp_path / "r.md", runner=fake_runner())
    assert export.export(private, tmp_path / "pub", terms, tmp_path / "r.md", runner=fake_runner(), allow_dirty=True, write_public=False)["ok"] is True


def test_a_dry_run_scans_and_reports_but_writes_no_public_repository(private, tmp_path, terms):
    result = export.export(private, tmp_path / "pub", terms, tmp_path / "r.md", runner=fake_runner(), write_public=False)
    assert result["ok"] is True and "public" not in result and not (tmp_path / "pub").exists() and (tmp_path / "r.md").exists()


def test_a_later_export_makes_a_branch_with_the_changes(private, tmp_path, terms):
    public = tmp_path / "public"
    export.export(private, public, terms, tmp_path / "r.md", runner=fake_runner())
    write(private, "ops/tool.py", "print('new')\n")
    write(private, "docs/RUNBOOK.md", "pause and stop and more\n")
    sh(private, "add", "-A")
    sh(private, "commit", "-q", "-m", "more")
    out = export.export(private, public, terms, tmp_path / "r.md", runner=fake_runner())["public"]
    assert out["committed"] and out["branch"].startswith("sync/") and sh(public, "rev-parse", "--abbrev-ref", "HEAD") == out["branch"]
    assert sh(public, "rev-list", "--count", "main") == "1" and (public / "ops" / "tool.py").read_text() == "print('new')\n"
    assert sh(public, "log", "-1", "--format=%s").startswith("Sync from the private repository")


def test_a_later_export_with_no_change_makes_no_commit_and_leaves_no_empty_branch(private, tmp_path, terms):
    public = tmp_path / "public"
    export.export(private, public, terms, tmp_path / "r.md", runner=fake_runner())
    again = export.export(private, public, terms, tmp_path / "r.md", runner=fake_runner())["public"]
    assert again == {"branch": "main", "committed": False, "fresh": False}
    assert sh(public, "branch", "--list").replace("*", "").split() == ["main"] and sh(public, "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_a_public_repository_with_uncommitted_changes_is_refused(private, tmp_path, terms):
    public = tmp_path / "public"
    export.export(private, public, terms, tmp_path / "r.md", runner=fake_runner())
    write(public, "stray.txt", "x\n")
    with pytest.raises(export.ExportError):
        export.export(private, public, terms, tmp_path / "r.md", runner=fake_runner())


def test_files_that_the_private_repository_removed_leave_the_public_repository(private, tmp_path, terms):
    public = tmp_path / "public"
    export.export(private, public, terms, tmp_path / "r.md", runner=fake_runner())
    sh(private, "rm", "-q", "tests/test_a.py")
    sh(private, "commit", "-q", "-m", "rm")
    export.export(private, public, terms, tmp_path / "r.md", runner=fake_runner())
    assert not (public / "tests" / "test_a.py").exists()


def test_the_github_identity_needs_a_login_and_an_id(tmp_path):
    assert export.github_identity(lambda cmd, **kw: Done(0, "octo 4242\n")) == ("octo", "4242+octo@users.noreply.github.com")
    for bad in (Done(1, ""), Done(0, "octo\n"), Done(0, "octo x\n")):
        with pytest.raises(export.ExportError):
            export.github_identity(lambda cmd, **kw: bad)


@pytest.mark.skipif(not (pathlib.Path(__file__).resolve().parents[2] / ".git").exists(), reason="needs the git index")
def test_the_real_manifest_never_selects_a_hard_denied_file():
    root = pathlib.Path(__file__).resolve().parents[2]
    if not (root / "public" / "manifest.txt").is_file():
        pytest.skip("this tree has no public/ folder: it is the public copy itself")
    include, exclude = export.read_manifest(root / "public" / "manifest.txt")
    tracked = export.tracked_files(root)
    chosen = [r for r in tracked if export.selected(r, include, exclude)]
    assert chosen and not [r for r in chosen if export.denied(r)]
    for rel in ("config/owner.yaml", "HANDOFF.md", "docs/DECISIONS.md", "BUILD_STATE.md", "NOTICE", "docs/SUDO_LOG.md"):
        assert rel not in chosen
