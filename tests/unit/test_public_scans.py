"""The scans of the public export. Planted values are built at run time, so this file holds no address, token, or private name. See D-051."""
import json
import pathlib
import subprocess

import pytest

from ops.public import scans

DOT = "."
IP = DOT.join(["192", "168", "1", "20"])
TAIL_IP = DOT.join(["100", "101", "5", "9"])
TS_NAME = "my-box" + DOT + "tail1234" + DOT + "ts" + DOT + "net"
SLACK_TOKEN = "xox" + "b-" + "1234567890-abcdefghij"
GH_TOKEN = "gh" + "p_" + "a" * 36
AWS = "AK" + "IA" + "A" * 16
TG_TOKEN = "123456789" + ":" + "A" * 35
DISCORD = "1" * 18
SLACK_ID = "U" + "0" + "ABCDEFGH1"
EMAIL = "someone" + "@" + "company" + DOT + "io"
KEY_HEADER = "-----BEGIN " + "RSA PRIVATE KEY-----"


def rules(text, terms=(), allow=(), rel="a.txt"):
    return sorted({rule for _r, _n, rule in scans.scan_text(rel, text, terms, allow)})


@pytest.mark.parametrize("text,rule", [
    ("host is " + IP, "private-ip"), ("addr " + TAIL_IP, "tailscale-ip"), ("name " + TS_NAME, "tailnet-name"),
    ("token " + SLACK_TOKEN, "slack-token"), ("token " + GH_TOKEN, "github-token"), ("key " + AWS, "aws-key"),
    ("bot " + TG_TOKEN, "telegram-token"), ("channel " + DISCORD, "discord-id"), ("member " + SLACK_ID, "slack-id"),
    ("mail " + EMAIL, "email"), ("-- " + KEY_HEADER, "private-key"),
    ("path /home/" + "zorblax" + "/notes", "home-path"), ("path C:" + "\\Users\\" + "zorblax" + "\\Desktop", "windows-path"),
    ("jwt " + "ey" + "J" + "a" * 12 + "." + "b" * 12 + "." + "c" * 8, "jwt"),
])
def test_each_generic_pattern_is_found(text, rule):
    assert rule in rules(text)


@pytest.mark.parametrize("text", [
    "contact you@example.com or a@example.org", "Co-Authored-By: Name <12345+login@users.noreply.github.com>", "git clone git@github.com:org/repo.git",
    "path /home/fleet/.hermes and /home/fleetops and /home/node/files", "path C:" + "\\Users\\" + "YourName" + "\\x",
    "http://127.0.0.1:5678 and 0.0.0.0", "version 2.102.0 and n8n-mcp@2.102.0 and image@sha256:" + "ab" * 32, "id 12345678", "number 1234567890123456",
    "docker python:3.13-slim-trixie@sha256:" + "e4" * 32, "plain words only",
])
def test_ordinary_text_is_clean(text):
    assert rules(text) == []


def test_the_deny_list_matches_in_any_case_and_a_hit_never_shows_the_term():
    found = scans.scan_text("docs/a.md", "Written by Alice Example here.\nnothing\n", terms=["alice example"])
    assert found == [("docs/a.md", 1, "deny-list")]
    assert "alice" not in repr(found).lower()


def test_short_terms_and_blank_lines_are_ignored_when_the_list_loads(tmp_path):
    path = tmp_path / "terms.txt"
    path.write_text("# comment\n\nab\nLonger Term\nlonger term\nxyz\n")
    assert scans.load_terms(path) == ["longer term"]
    path.write_text("ab\n")
    with pytest.raises(scans.ScanError):
        scans.load_terms(path)
    with pytest.raises(scans.ScanError):
        scans.load_terms(tmp_path / "missing.txt")


def test_the_allow_list_clears_one_rule_in_one_path_only():
    allow = [("tests/*", "private-ip")]
    assert rules("x " + IP, allow=allow, rel="tests/a.py") == []
    assert rules("x " + IP, allow=allow, rel="src/a.py") == ["private-ip"]
    assert rules("x " + EMAIL, allow=allow, rel="tests/a.py") == ["email"]


def test_scan_tree_reads_text_files_and_names_and_skips_binary_and_git(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("fine\nhost " + IP + "\n")
    (tmp_path / "bin.dat").write_bytes(b"\x00\x01" + IP.encode())
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text(IP)
    (tmp_path / "secret-name-here.txt").write_text("ok")
    found = scans.scan_tree(tmp_path, terms=["secret-name"])
    assert found == [("docs/a.md", 2, "private-ip"), ("secret-name-here.txt", 0, "deny-list")]


def test_a_big_file_is_skipped(tmp_path):
    (tmp_path / "big.txt").write_text(("x" * 100 + "\n") * 25_000 + IP)
    assert scans.scan_tree(tmp_path) == []


def test_summarize_counts_by_rule():
    assert scans.summarize([("a", 1, "email"), ("b", 2, "email"), ("c", 0, "deny-list")]) == {"deny-list": 1, "email": 2}


class Done:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_gitleaks_findings_are_read_from_the_report_and_carry_no_secret(tmp_path):
    def runner(cmd, **kw):
        report = pathlib.Path(cmd[cmd.index("--report-path") + 1])
        report.write_text(json.dumps([{"File": str(tmp_path / "x" / "a.py"), "StartLine": 7, "RuleID": "generic-api-key", "Secret": "REDACTED"}]))
        assert "--redact" in cmd and "--no-git" in cmd and cmd[cmd.index("--source") + 1] == "." and kw["cwd"] == str(tmp_path)
        return Done(1)
    assert scans.run_gitleaks(tmp_path, runner=runner) == [("x/a.py", 7, "gitleaks:generic-api-key")]
    relative = lambda cmd, **kw: (pathlib.Path(cmd[cmd.index("--report-path") + 1]).write_text(json.dumps([{"File": "vendor/pins.env", "StartLine": 2, "RuleID": "r"}])), Done(1))[1]
    assert scans.run_gitleaks(tmp_path, runner=relative) == [("vendor/pins.env", 2, "gitleaks:r")]
    assert scans.run_gitleaks(tmp_path, runner=lambda cmd, **kw: Done(0)) == []
    with pytest.raises(scans.ScanError):
        scans.run_gitleaks(tmp_path, runner=lambda cmd, **kw: Done(2))


def test_osv_findings_name_the_lock_file_and_the_package(tmp_path):
    out = json.dumps({"results": [{"source": {"path": str(tmp_path / "vendor" / "package-lock.json")},
                                   "packages": [{"package": {"name": "lodash"}, "vulnerabilities": [{"id": "X"}]}, {"package": {"name": "ok"}, "vulnerabilities": []}]}]})
    assert scans.run_osv(tmp_path, runner=lambda cmd, **kw: Done(1, out)) == [("vendor/package-lock.json", 0, "osv:lodash")]
    assert scans.run_osv(tmp_path, runner=lambda cmd, **kw: Done(0, "{}")) == []
    with pytest.raises(scans.ScanError):
        scans.run_osv(tmp_path, runner=lambda cmd, **kw: Done(127))
    with pytest.raises(scans.ScanError):
        scans.run_osv(tmp_path, runner=lambda cmd, **kw: Done(0, "{ not json"))
