"""The deny-list builder. Names here are made up. The file is written with mode 600 and holds no value in any output."""
import json
import os
import pathlib
import subprocess

import pytest

from ops.public import terms

ENV = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")

DOT = "."
EMAIL = "quill.m" + "@" + "corp-example" + DOT + "io"
IP = DOT.join(["10", "9", "8", "7"])
TAIL_IP = DOT.join(["100", "101", "102", "103"])
SUFFIX = "tail9876" + DOT + "ts" + DOT + "net"
DNS = "mybox" + DOT + SUFFIX
CHANNEL_ID = "C" + "0" + "HQ12345678"


class Done:
    def __init__(self, returncode=0, stdout=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, ""


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.name", "Quill Marmoset"], ["config", "user.email", EMAIL]):
        subprocess.run(["git", "-C", str(root)] + args, check=True, capture_output=True, env=ENV)  # nosemgrep: dangerous-subprocess-use-audit
    (root / "a.txt").write_text("x\n")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True, env=ENV)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Quill Marmoset", "-c", "user.email=" + EMAIL, "commit", "-q", "-m", "x"], check=True, capture_output=True, env=ENV)
    (root / "projects" / "bakery-bot").mkdir(parents=True)
    (root / "projects" / "project-001").mkdir()
    (root / "projects" / "_studio" / "brand").mkdir(parents=True)
    (root / "projects" / "_studio" / "brand" / "brand.yaml").write_text("name: Flapjack Works\n")
    (root / "docs").mkdir()
    (root / "docs" / "n.md").write_text("see " + "C:" + "\\Users\\" + "quillw" + "\\x\n")
    return root


def fake(cmd, **kw):
    if cmd[0] == "ip":
        return Done(0, json.dumps([{"addr_info": [{"local": "127.0.0.1"}, {"local": IP}, {"local": "fe80::1"}]}]))
    if cmd[:2] == ["tailscale", "ip"]:
        return Done(0, TAIL_IP + "\n")
    if cmd[:2] == ["tailscale", "status"]:
        return Done(0, json.dumps({"MagicDNSSuffix": SUFFIX, "Self": {"DNSName": DNS + "."}}))
    if cmd[0] == "gh":
        return Done(0, "quillm-gh\n")
    return subprocess.run(cmd, **dict(kw, env=ENV))  # nosemgrep: dangerous-subprocess-use-audit


def test_the_list_holds_names_emails_hosts_addresses_handles_projects_and_brand(repo, tmp_path):
    overrides = {"owner": {"name": "Quill Marmoset", "email": EMAIL, "telegram_user_ids": [1234567890], "timezone": "America/Chicago"},
                 "slack": {"channels": {"hq": CHANNEL_ID}}, "publisher": {"mode": "live"}, "ports": {"router": 20128}}
    found = terms.collect(repo, tmp_path, fake, overrides=overrides, owner_file={"owner": {"name": "Quill Marmoset"}})
    lowered = {t.lower() for t in found}
    for want in ("quill marmoset", "marmoset", EMAIL, "quill.m", IP, TAIL_IP, SUFFIX, DNS,
                 "quillm-gh", "bakery-bot", "flapjack works", "flapjack", "quillw", "1234567890", CHANNEL_ID.lower()):
        assert want in lowered, want


def test_generic_values_and_loopback_are_not_in_the_list(repo, tmp_path):
    found = terms.collect(repo, tmp_path, fake, overrides={"owner": {"timezone": "America/Chicago"}, "publisher": {"mode": "live"}, "ports": {"router": 20128}, "f": True})
    lowered = {t.lower() for t in found}
    assert "project-001" not in lowered and "127.0.0.1" not in lowered and "america/chicago" not in lowered and "live" not in lowered and "20128" not in lowered
    assert not any(t.startswith("fe80") for t in found) and all(len(t) >= 4 for t in found)


def test_a_failing_source_adds_nothing_and_does_not_stop_the_build(repo, tmp_path):
    def broken(cmd, **kw):
        if cmd[0] in ("ip", "tailscale", "gh"):
            raise OSError("missing")
        return subprocess.run(cmd, **dict(kw, env=ENV))  # nosemgrep: dangerous-subprocess-use-audit
    found = terms.collect(repo, tmp_path, broken)
    assert "quill marmoset" in {t.lower() for t in found}


def test_extra_terms_that_the_owner_adds_by_hand_are_kept(repo, tmp_path):
    extra = tmp_path / "extra.txt"
    extra.write_text("# mine\nmy-private-domain.example\n")
    assert "my-private-domain.example" in terms.collect(repo, tmp_path, fake, extra_file=extra)


def test_write_makes_the_file_and_its_folder_private_and_returns_only_the_count(tmp_path):
    path = terms.terms_path(tmp_path)
    count = terms.write({"Alpha Term", "beta-term"}, path)
    assert count == 2 and oct(path.stat().st_mode & 0o777) == "0o600" and oct(path.parent.stat().st_mode & 0o777) == "0o700"
    assert path.read_text().splitlines()[1:] == ["Alpha Term", "beta-term"] and path.parent.name == "agent-fleet" and path.parent.parent.name == ".config"


# ---- D-059: only personal paths of the live config become terms
def test_provider_names_modes_and_other_public_settings_of_the_live_config_are_not_terms(repo, tmp_path):
    overrides = {"router": {"approved_providers": ["groq", "openrouter", "nvidia", "cohere"], "allow_noncommercial": True, "strategy": "priority"},
                 "deployer": {"mode": "dry-run"}, "briefing": {"hour": "07:00", "email": {"smtp_host": "smtp.example.org", "smtp_user": "quill@example.org"}},
                 "quota": {"threshold_percent": 80}, "slack": {"sink_mode": True, "quiet_hours": {"start": "22:00", "end": "07:00"}},
                 "owner": {"name": "Quill Marmoset"}}
    lowered = {t.lower() for t in terms.collect(repo, tmp_path, fake, overrides=overrides)}
    for common in ("groq", "openrouter", "nvidia", "cohere", "priority", "dry-run", "22:00", "07:00"):
        assert common not in lowered, common
    assert "quill marmoset" in lowered and "marmoset" in lowered                 # the owner path still counts


def test_handles_hosts_and_repos_under_the_personal_paths_are_terms(repo, tmp_path):
    overrides = {"publisher": {"platforms": {"bluesky": {"handle": "bakery-bot.example.social", "enabled": True},
                                             "github": {"repo": "flapjackworks/waffle-iron"}}},
                 "backup": {"github_remote": "https://github.com/flapjackworks/fleet-backup.git"},
                 "briefing": {"email": {"smtp_user": "quill.m@mailer.example"}}}
    lowered = {t.lower() for t in terms.collect(repo, tmp_path, fake, overrides=overrides)}
    for want in ("bakery-bot.example.social", "flapjackworks/waffle-iron", "https://github.com/flapjackworks/fleet-backup.git", "quill.m@mailer.example", "quill.m"):
        assert want in lowered, want


def test_the_private_owner_file_is_read_in_full(repo, tmp_path):
    found = terms.collect(repo, tmp_path, fake, owner_file={"anything": {"deep": ["pancake-lab", "waffle-iron-9"]}})
    assert {"pancake-lab", "waffle-iron-9"} <= {t.lower() for t in found}
