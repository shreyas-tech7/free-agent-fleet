import os
import stat

import pytest

from ops.fleetlib import config, secrets, tokens
from ops.n8n import prepare as prep
from tests.unit.fctl_helpers import Runner

IMAGE = "docker.n8n.io/n8nio/n8n:2.47.4@sha256:" + "ab" * 32
PINS = {"N8N_IMAGE": IMAGE, "N8N_VERSION": "2.47.4"}
HASH = "$2a$10$" + "A" * 53


def good_runner(extra_rules=()):
    """A runner that answers the image check, the pull, and the bcrypt step."""
    return Runner(rules=list(extra_rules) + [
        (["image", "inspect"], (0, "")),
        (["--entrypoint"], (0, "%s\nverify-ok" % HASH)),
    ])


def no_git(key):
    return ""


@pytest.fixture(autouse=True)
def no_real_ports(monkeypatch):
    monkeypatch.setattr(prep, "port_in_use", lambda port: False)


# ------------------------------------------------------------------ owner identity
def test_split_name():
    assert prep.split_name("Ada Lovelace King") == ("Ada", "Lovelace King")
    assert prep.split_name("Ada") == ("Ada", "Account")
    assert prep.split_name("") == ("Owner", "Account")
    assert prep.split_name("Zoë O'Neil") == ("Zoë", "ONeil")                     # no quote in a value that goes to a file


def test_identity_prefers_git_then_the_live_config_then_a_placeholder():
    cfg = {"owner": {"email": "cfg@example.org", "name": "Config Person"}}
    git = {"user.email": "git@example.org", "user.name": "Git Person"}.get
    assert prep.owner_identity(cfg, lambda k: git(k, "")) == ("git@example.org", "Git", "Person")
    assert prep.owner_identity(cfg, no_git) == ("cfg@example.org", "Config", "Person")
    assert prep.owner_identity({"owner": {"email": "", "name": "Your Name"}}, no_git) == (prep.PLACEHOLDER_EMAIL, "Owner", "Account")
    assert prep.owner_identity({"owner": {"email": "not-an-email"}}, lambda k: "bad" if k == "user.email" else "") [0] == prep.PLACEHOLDER_EMAIL


def test_git_value_reads_the_config_of_the_person_who_ran_sudo(monkeypatch):
    r = Runner(rules=[(["config"], (0, "me@example.org\n"))])
    monkeypatch.delenv("SUDO_USER", raising=False)
    assert prep.git_value("user.email", r) == ""                                   # nobody to ask
    monkeypatch.setenv("SUDO_USER", "alice")
    assert prep.git_value("user.email", r) == "me@example.org"
    assert r.calls[-1][1] == "alice"                                               # it ran as that user
    monkeypatch.setenv("SUDO_USER", "root")
    assert prep.git_value("user.email", Runner()) == ""


# ------------------------------------------------------------------ env file
def test_render_env_has_the_pins_and_no_secret():
    text = prep.render_env(PINS, ("a@example.org", "Ada", "King"), "America/Chicago", "1g")
    assert "N8N_IMAGE=%s" % IMAGE in text and "N8N_VERSION=2.47.4" in text
    assert "N8N_MEMORY=1g" in text and "N8N_MEMORY_SWAP=1536m" in text
    assert "N8N_OWNER_EMAIL=a@example.org" in text and "N8N_SECRETS_FILE=/etc/agent-fleet/secrets/n8n.env" in text
    assert "ENCRYPTION" not in text and "PASSWORD" not in text and "HASH" not in text and "TOKEN" not in text


def test_render_env_keeps_the_tailscale_values_from_an_earlier_file():
    old = {"N8N_EDITOR_BASE_URL": "https://box.example.ts.net:8443", "WEBHOOK_URL": "https://box.example.ts.net:8443/", "N8N_PROXY_HOPS": "1", "OTHER": "x"}
    text = prep.render_env(PINS, ("a@example.org", "Ada", "King"), "UTC", "768m", old)
    assert "N8N_PROXY_HOPS=1" in text and "N8N_EDITOR_BASE_URL=https://box.example.ts.net:8443" in text and "OTHER" not in text
    assert "N8N_MEMORY_SWAP=1152m" in text


def test_swap_limit_and_bad_memory():
    assert prep.swap_limit("2g") == "3072m" and prep.swap_limit("512m") == "768m"
    with pytest.raises(prep.PrepareError):
        prep.swap_limit("lots")


def test_read_pins_needs_the_n8n_lines(tmp_path):
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "pins.env").write_text("# c\nDOCKER_VERSION=1\n")
    with pytest.raises(prep.PrepareError):
        prep.read_pins(tmp_path)
    (tmp_path / "vendor" / "pins.env").write_text("N8N_IMAGE=img\nN8N_VERSION=2.1.0\n")
    assert prep.read_pins(tmp_path) == {"N8N_IMAGE": "img", "N8N_VERSION": "2.1.0"}


def test_the_real_pins_file_pins_n8n_by_version_and_digest():
    pins = prep.read_pins()
    assert pins["N8N_VERSION"].startswith("2.") and "@sha256:" in pins["N8N_IMAGE"] and pins["N8N_IMAGE"].startswith("docker.n8n.io/n8nio/n8n:")
    assert pins["N8N_IMAGE"].split(":")[1].split("@")[0] == pins["N8N_VERSION"]


# ------------------------------------------------------------------ the owner password hash
def test_the_password_goes_only_to_standard_input(fleet_env):
    r = good_runner()
    assert prep.bcrypt_hash("s3cret-pass-0123456789", IMAGE, r) == HASH
    argv, user = r.calls[0]
    assert user == "fleetops" and r.inputs[0] == "s3cret-pass-0123456789"
    assert not any("s3cret" in a for a in argv)                                     # never in an argument


@pytest.mark.parametrize("answer", [(1, ""), (0, "garbage"), (0, "%s\nverify-FAILED" % HASH), (0, "$2a$10$short\nverify-ok")])
def test_a_bad_hash_answer_is_refused(fleet_env, answer):
    with pytest.raises(prep.PrepareError):
        prep.bcrypt_hash("pw-0123456789012345678", IMAGE, Runner(default=answer))


def test_the_folders_that_the_workflows_write_to_exist_and_are_private(fleet_env):
    """n8n does not make the folder of a file that it writes. The heartbeat, the ledger, and the unsent alerts each need one."""
    made = prep.ensure_dirs()
    files = prep.run_dir() / "files"
    for sub in ("heartbeat", "ledger", "unsent", "unsent/sent", "unsent/bad"):
        assert (files / sub).is_dir() and stat.S_IMODE((files / sub).stat().st_mode) == 0o700, sub
        assert ("files/" + sub) in made
    assert prep.ensure_dirs() == []


def test_the_owner_hash_is_made_once_and_made_again_when_the_password_changes(fleet_env):
    fleet_env["secrets"].mkdir(exist_ok=True)
    secrets.set_value("N8N_OWNER_PASSWORD", "p" * 30)
    r = good_runner()
    assert prep.ensure_owner_hash(IMAGE, r) == "made"
    assert secrets.value_of("N8N_INSTANCE_OWNER_PASSWORD_HASH") == HASH
    assert prep.ensure_owner_hash(IMAGE, r) == "kept" and len(r.calls) == 1        # no second container run
    secrets.set_value("N8N_OWNER_PASSWORD", "q" * 30)
    assert prep.ensure_owner_hash(IMAGE, r) == "made" and len(r.calls) == 2


def test_the_owner_hash_needs_a_password(fleet_env):
    with pytest.raises(prep.PrepareError):
        prep.ensure_owner_hash(IMAGE, good_runner())


# ------------------------------------------------------------------ the whole step
def test_prepare_makes_folders_secrets_hash_env_and_pulls_the_image(fleet_env):
    r = Runner(rules=[(["image", "inspect"], (1, "no such image")), (["pull"], (0, "")), (["--entrypoint"], (0, "%s\nverify-ok" % HASH))])
    report = prep.prepare({"owner": {"email": "o@example.org", "name": "Own Er"}, "n8n": {"memory": "1g", "timezone": "America/Chicago"}},
                          r, git=no_git, pins=PINS, unit_active=True)
    assert report["image"] == "pulled" and report["owner_hash"] == "made" and report["env"] == "written"
    assert set(report["secrets"]) >= {"N8N_ENCRYPTION_KEY", "N8N_OWNER_PASSWORD", "FLEET_EVENT_TOKEN", "FLEET_ACTION_TOKEN", "N8N_TOOLS_TOKEN"}
    base = prep.run_dir()
    assert {p.name for p in base.iterdir()} >= {"data", "files", "backups", "exports", ".env"}
    assert stat.S_IMODE((base / ".env").stat().st_mode) == 0o600 and stat.S_IMODE(base.stat().st_mode) == 0o700
    spool = base.parent / "events" / "spool"
    assert spool.is_dir() and stat.S_IMODE(spool.stat().st_mode) == 0o770 and (base.parent / "events" / "bad").is_dir()
    state = fleet_env["state"]                                  # the agents' side: a spool for the fleet user and the card inbox
    assert (state / "events" / "spool").is_dir() and stat.S_IMODE((state / "cards-in").stat().st_mode) == 0o2770
    assert (state / "cards-in" / "bad").is_dir()
    env_text = (base / ".env").read_text()
    for name in ("N8N_ENCRYPTION_KEY", "N8N_OWNER_PASSWORD", "FLEET_EVENT_TOKEN", "FLEET_ACTION_TOKEN", "N8N_TOOLS_TOKEN"):
        assert secrets.value_of(name) not in env_text                                 # no secret value in the compose .env
    assert HASH not in env_text
    for name in ("N8N_ENCRYPTION_KEY", "N8N_OWNER_PASSWORD", "FLEET_EVENT_TOKEN", "FLEET_ACTION_TOKEN"):
        assert stat.S_IMODE(secrets.secret_path(name).stat().st_mode) == 0o600
    assert tokens.hashes("FLEET_ACTION_TOKEN")                                         # the control bot holds only the hash
    assert secrets.value_of("N8N_TOOLS_TOKEN") in (fleet_env["hermes"] / ".env").read_text()


def test_prepare_twice_changes_nothing(fleet_env):
    r = good_runner()
    cfg = {"owner": {"email": "o@example.org", "name": "Own Er"}}
    prep.prepare(cfg, r, git=no_git, pins=PINS, unit_active=True)
    key = secrets.value_of("N8N_ENCRYPTION_KEY")
    calls = len(r.calls)
    second = prep.prepare(cfg, r, git=no_git, pins=PINS, unit_active=True)
    assert second["secrets"] == [] and second["owner_hash"] == "kept" and second["image"] == "present"
    assert secrets.value_of("N8N_ENCRYPTION_KEY") == key                              # the encryption key never changes by itself
    assert len(r.calls) == calls + 1                                                  # only the image check ran again


def test_a_used_port_is_a_warning_only_when_the_unit_is_down(fleet_env, monkeypatch):
    monkeypatch.setattr(prep, "port_in_use", lambda port: port == 5679)
    cfg = {"owner": {"email": "o@example.org", "name": "Own Er"}}
    down = prep.prepare(cfg, good_runner(), git=no_git, pins=PINS, unit_active=False)
    assert down["warnings"] == ["port 5679 is in use and the n8n unit is not running"]
    up = prep.prepare(cfg, good_runner(), git=no_git, pins=PINS, unit_active=True)
    assert up["warnings"] == []


def test_the_encryption_key_is_never_rotated_by_fleetctl():
    from ops.fleetctl import secretcmd
    assert "N8N_ENCRYPTION_KEY" in secretcmd.NEVER_ROTATE
