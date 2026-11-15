import os
import stat

import pytest

from ops.fleetlib import secrets, redact

GROQ = "gsk_" + "A1b2C3d4E5" * 3


def test_registry_is_consistent():
    names = [s["name"] for s in secrets.all_secrets()]
    assert len(names) == len(set(names)), "secret names must be unique"
    groups = secrets.registry()["groups"]
    for s in secrets.all_secrets():
        assert s["group"] in groups
        assert s["file"].startswith(("/etc/agent-fleet/secrets/", "/home/fleet/.hermes/"))
        # Each file belongs to the user of the one service that reads it. Two readers run as root: the tests that use the
        # dev key, and fleetctl for n8n (the owner password, the API key, and the action token).
        assert s["owner"] in ("fleet", "fleetops") or (s["owner"] == "root" and s["service"] in ("dev-tests", "n8n-admin"))
        assert s["service"], "each secret has one reader"


def test_each_service_file_has_one_owner():
    owners = {}
    for s in secrets.all_secrets():
        owners.setdefault(s["file"], set()).add(s["owner"])
    assert all(len(v) == 1 for v in owners.values())


def test_set_value_writes_mode_600_and_preserves_other_lines(fleet_env):
    path = secrets.secret_path("GROQ_API_KEY")
    path.write_text("# keep me\nOTHER=1\n")
    secrets.set_value("GROQ_API_KEY", GROQ)
    assert (path.stat().st_mode & 0o777) == 0o600
    text = path.read_text()
    assert "# keep me" in text and "OTHER=1" in text and ("GROQ_API_KEY=" + GROQ) in text
    secrets.set_value("GROQ_API_KEY", "gsk_" + "Z9y8X7w6V5" * 3)      # replace, no duplicate line
    assert path.read_text().count("GROQ_API_KEY=") == 1


def test_format_check_rejects_without_echoing_value(fleet_env):
    with pytest.raises(secrets.SecretError) as err:
        secrets.set_value("GROQ_API_KEY", "not-a-groq-key-1234567890")
    assert "not-a-groq-key" not in str(err.value)
    assert not secrets.is_set("GROQ_API_KEY")


def test_rejects_line_breaks_and_unknown_names(fleet_env):
    ok, _ = secrets.check_format("GROQ_API_KEY", GROQ + "\nEVIL=1")
    assert not ok
    with pytest.raises(secrets.SecretError):
        secrets.set_value("NOT_A_SECRET", "x")


def test_is_set_and_value_of(fleet_env):
    assert secrets.is_set("GROQ_API_KEY") is False
    secrets.set_value("GROQ_API_KEY", GROQ)
    assert secrets.is_set("GROQ_API_KEY") is True
    assert secrets.value_of("GROQ_API_KEY") == GROQ
    assert redact.redact_text("key " + GROQ) == "key [REDACTED]"     # registered for redaction


def test_quoting_round_trip(fleet_env):
    secrets.set_value("SMTP_PASSWORD", "pa ss#word!1", enforce_format=True)
    path = secrets.secret_path("SMTP_PASSWORD")
    assert "SMTP_PASSWORD='pa ss#word!1'" in path.read_text()
    assert secrets.read_values(path)["SMTP_PASSWORD"] == "pa ss#word!1"
    with pytest.raises(secrets.SecretError):
        secrets.set_value("SMTP_PASSWORD", "has'quote-1234")


def test_unset_value(fleet_env):
    secrets.set_value("GROQ_API_KEY", GROQ)
    assert secrets.unset_value("GROQ_API_KEY") is True
    assert secrets.is_set("GROQ_API_KEY") is False
    assert secrets.unset_value("GROQ_API_KEY") is False


def test_hermes_env_goes_to_the_agent_home(fleet_env):
    secrets.set_value("PUBLISHER_API_TOKEN", "A" * 40)
    expected = fleet_env["hermes"] / "profiles" / "marketing" / ".env"
    assert expected.exists()
    assert (stat.S_IMODE(expected.stat().st_mode)) == 0o600


def test_status_rows_never_hold_values(fleet_env):
    secrets.set_value("GROQ_API_KEY", GROQ)
    rows = secrets.status_rows("router")
    assert all(GROQ not in str(r) for r in rows)
    assert {r["name"]: r["set"] for r in rows}["GROQ_API_KEY"] is True


def test_also_files_get_the_same_value(fleet_env):
    key = "k" * 40
    for prof in ("marketing", "build"):
        (fleet_env["hermes"] / "profiles" / prof / ".env").write_text("# profile file\nOTHER=1\n")
    path = secrets.set_value("ROUTER_KEY_HERMES", key)
    assert path == fleet_env["hermes"] / ".env"
    for prof in ("marketing", "build"):
        text = (fleet_env["hermes"] / "profiles" / prof / ".env").read_text()
        assert "ROUTER_KEY_HERMES=" + key in text and "OTHER=1" in text


def test_rotating_a_local_token_stores_the_token_for_the_agent_and_only_a_hash_for_the_service(fleet_env):
    from ops.fleetlib import httpd, tokens
    tokens.rotate("PUBLISHER_API_TOKEN")
    token = secrets.value_of("PUBLISHER_API_TOKEN")
    service_text = (fleet_env["secrets"] / "publisher.env").read_text()
    assert token not in service_text and "PUBLISHER_API_TOKEN_SHA256=" in service_text
    assert tokens.hashes("PUBLISHER_API_TOKEN") == [httpd.token_hash(token)]
    old = token
    tokens.rotate("PUBLISHER_API_TOKEN")
    assert secrets.value_of("PUBLISHER_API_TOKEN") != old
    assert len(tokens.hashes("PUBLISHER_API_TOKEN")) == 1          # the old hash is gone
    with pytest.raises(secrets.SecretError):
        tokens.rotate("GROQ_API_KEY")


# ---- D-053: a token without its hash, and the order of the two writes
def test_the_hash_is_written_before_the_token(fleet_env, monkeypatch):
    from ops.fleetlib import tokens
    order = []
    real_update, real_set = secrets._update_file, secrets.set_value
    monkeypatch.setattr(secrets, "_update_file", lambda *a, **k: order.append("hash") or real_update(*a, **k))
    monkeypatch.setattr(secrets, "set_value", lambda *a, **k: order.append("token") or real_set(*a, **k))
    tokens.rotate("FLEET_ACTION_TOKEN")
    assert order[0] == "hash" and order.index("hash") < order.index("token")      # set_value writes a file too, so look at the first hash write


def test_a_failed_token_write_never_leaves_a_token_that_the_service_refuses(fleet_env, monkeypatch):
    from ops.fleetlib import tokens
    monkeypatch.setattr(secrets, "set_value", lambda *a, **k: (_ for _ in ()).throw(secrets.SecretError("disk full")))
    with pytest.raises(secrets.SecretError):
        tokens.rotate("FLEET_ACTION_TOKEN")
    assert not secrets.is_set("FLEET_ACTION_TOKEN") and not tokens.matches("FLEET_ACTION_TOKEN")      # no token, so nothing to refuse


def test_a_token_without_its_hash_gets_the_hash_again_and_keeps_its_value(fleet_env):
    from ops.fleetlib import tokens
    token = "t" * 48
    secrets.set_value("FLEET_ACTION_TOKEN", token)                     # the state that the live machine had: the token, and no hash
    assert tokens.hashes("FLEET_ACTION_TOKEN") == [] and not tokens.matches("FLEET_ACTION_TOKEN")
    assert tokens.repair("FLEET_ACTION_TOKEN") is True
    assert secrets.value_of("FLEET_ACTION_TOKEN") == token and tokens.matches("FLEET_ACTION_TOKEN")
    assert tokens.repair("FLEET_ACTION_TOKEN") is False                # nothing to do the second time


def test_repair_replaces_a_wrong_hash_and_does_nothing_without_a_token(fleet_env):
    from ops.fleetlib import tokens
    assert tokens.repair("FLEET_ACTION_TOKEN") is False                # no token yet
    secrets.set_value("FLEET_ACTION_TOKEN", "u" * 48)
    file, hash_var, owner = tokens.DERIVED["FLEET_ACTION_TOKEN"]
    secrets._update_file(secrets.resolve_path(file), hash_var, "%s=%s" % (hash_var, "0" * 64), owner)
    assert not tokens.matches("FLEET_ACTION_TOKEN")
    assert tokens.repair("FLEET_ACTION_TOKEN") is True and tokens.matches("FLEET_ACTION_TOKEN")
    with pytest.raises(secrets.SecretError):
        tokens.repair("GROQ_API_KEY")


def test_ensure_repairs_a_token_that_lost_its_hash_and_says_so(fleet_env):
    from ops.fleetlib import autosecrets, tokens
    secrets.set_value("FLEET_ACTION_TOKEN", "v" * 48)
    repaired = []
    made = autosecrets.ensure(["FLEET_ACTION_TOKEN", "FLEET_EVENT_TOKEN"], repaired=repaired)
    assert repaired == ["FLEET_ACTION_TOKEN"] and tokens.matches("FLEET_ACTION_TOKEN") and secrets.value_of("FLEET_ACTION_TOKEN") == "v" * 48
    assert "FLEET_ACTION_TOKEN" not in made
    again = []
    autosecrets.ensure(["FLEET_ACTION_TOKEN"], repaired=again)
    assert again == []
