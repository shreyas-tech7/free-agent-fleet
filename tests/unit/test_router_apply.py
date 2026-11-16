import json

import pytest

from ops.fleetlib import config, secrets
from ops.router import apply as ra
from tests.fake_omniroute import FakeOmniRoute

GROQ = "gsk_" + "A1b2C3d4E5" * 3


@pytest.fixture
def setup(fleet_env):
    # router password and the two client key files
    secrets.set_value("INITIAL_PASSWORD", "pw-test-123-abcdefgh", enforce_format=False)
    fake = FakeOmniRoute(password="pw-test-123-abcdefgh", provider_models={
        "groq": ["openai/gpt-oss-20b", "qwen/qwen3.6-27b"],
    })
    with fake:
        router = ra.Router(fake.url, "pw-test-123-abcdefgh")
        yield fleet_env, fake, router


def run(router, fleet_env, **kw):
    # Never reach the real OpenRouter list from a test. A test that needs the list passes its own.
    kw.setdefault("openrouter_models", lambda: None)
    return ra.apply(router, state_path=fleet_env["ops"] / "router" / "applied.json", **kw)


def approve(name):
    config.set_override("router.approved_providers", "[%s]" % name)


def test_first_run_applies_policy_makes_placeholder_combos_and_keys(setup):
    fleet_env, fake, router = setup
    report = run(router, fleet_env)
    assert fake.settings["freeAccessPolicy"] == "strict" and fake.settings["proxyEnabled"] is False
    assert report["providers"] == {}
    assert {c["name"] for c in fake.combos} == {"fleet-smart", "fleet-worker", "fleet-coder", "fleet-local", "fleet-dev"}
    assert all("placeholder" in v for v in report["combos"].values())
    assert report["keys"] == {"hermes": "created", "briefing": "created", "dev": "created"}
    # the new client keys landed in the right secret files, never in the report
    hermes_key = secrets.value_of("ROUTER_KEY_HERMES")
    assert hermes_key.startswith("sk-fake-") and hermes_key not in json.dumps(report)
    assert secrets.value_of("ROUTER_KEY_BRIEFING").startswith("sk-fake-")
    perm = next(k for k in fake.keys if k["name"] == "fleet-briefing")["perm"]
    assert perm["allowedCombos"] == ["fleet-worker"] and perm["allowAutoCombos"] is False


def test_a_provider_that_no_agent_combo_names_is_connected_and_the_combos_stay_placeholders(setup):
    """Groq is connected, but its free plan cannot carry an agent prompt. No agent combo names it. See D-064."""
    fleet_env, fake, router = setup
    approve("groq")
    secrets.set_value("GROQ_API_KEY", GROQ)
    report = run(router, fleet_env)
    assert report["providers"] == {"groq": "created"}
    assert fake.providers[0]["provider"] == "groq" and fake.providers[0]["apiKey"] == GROQ
    for name in ("fleet-smart", "fleet-worker", "fleet-coder"):
        combo = next(c for c in fake.combos if c["name"] == name)
        assert not any(m["model"].startswith("groq/") for m in combo["models"]), name
        assert "placeholder" in report["combos"][name], name
    local = next(c for c in fake.combos if c["name"] == "fleet-local")
    assert [m["model"] for m in local["models"]] == ["ollama/qwen3:1.7b"]       # placeholder only, no cloud fallback
    assert "placeholder" in report["combos"]["fleet-local"]


def test_no_agent_combo_in_the_real_config_names_a_groq_target():
    """The Groq free plan allows 8,000 tokens a minute. An agent call answers 413, and Hermes reads that as a conversation that is too large. See D-064."""
    combos = ra.load_combos()["combos"]
    for name in ("fleet-smart", "fleet-worker", "fleet-coder"):
        assert not [t for t in combos[name]["targets"] if t.startswith("groq/")], name


def test_second_run_changes_nothing(setup):
    fleet_env, fake, router = setup
    approve("groq")
    secrets.set_value("GROQ_API_KEY", GROQ)
    run(router, fleet_env)
    calls = len(fake.log)
    report = run(router, fleet_env)
    assert report["settings"] == "unchanged"
    assert report["providers"] == {"groq": "kept"}
    assert all(v.startswith("kept") for v in report["combos"].values())
    assert report["keys"] == {"hermes": "kept", "briefing": "kept", "dev": "kept"}
    assert len(fake.providers) == 1 and len(fake.keys) == 3
    assert len(fake.log) > calls


def test_rotated_key_updates_the_connection(setup):
    fleet_env, fake, router = setup
    approve("groq")
    secrets.set_value("GROQ_API_KEY", GROQ)
    run(router, fleet_env)
    new = "gsk_" + "Z9y8X7w6V5" * 3
    secrets.set_value("GROQ_API_KEY", new)
    report = run(router, fleet_env)
    assert report["providers"] == {"groq": "updated"} and fake.providers[0]["apiKey"] == new


def test_losing_approval_removes_the_connection(setup):
    fleet_env, fake, router = setup
    approve("groq")
    secrets.set_value("GROQ_API_KEY", GROQ)
    run(router, fleet_env)
    config.set_override("router.approved_providers", "[]")
    report = run(router, fleet_env)
    assert report["providers"] == {"groq": "removed"} and fake.providers == []


def test_provider_without_approval_is_never_connected(setup):
    fleet_env, fake, router = setup
    secrets.set_value("GROQ_API_KEY", GROQ)          # key exists, owner has not approved the terms
    report = run(router, fleet_env)
    assert report["providers"] == {} and fake.providers == []


def test_invalid_client_key_is_replaced_and_old_one_deleted(setup):
    fleet_env, fake, router = setup
    run(router, fleet_env)
    old_ids = {k["id"] for k in fake.keys}
    secrets.set_value("ROUTER_KEY_HERMES", "sk-not-valid-" + "x" * 30, enforce_format=False)
    report = run(router, fleet_env)
    assert report["keys"]["hermes"] == "created"
    assert len(fake.keys) == 3
    assert secrets.value_of("ROUTER_KEY_HERMES").startswith("sk-fake-")


def test_openrouter_paid_models_are_never_selected():
    combo = {"targets": ["openrouter/openai/gpt-4o", "openrouter/qwen/qwen3-coder:free"]}
    kept = ra.select_targets(combo, {"openrouter"}, {"openrouter": None})
    assert kept == ["openrouter/qwen/qwen3-coder:free"]


def test_local_only_combo_keeps_only_local_targets():
    combo = {"local_only": True, "targets": ["groq/x", "ollama/qwen3:1.7b"]}
    assert ra.select_targets(combo, {"groq"}, {}) == []
    assert ra.select_targets(combo, {"groq", "ollama"}, {}) == ["ollama/qwen3:1.7b"]


def test_wrong_password_raises_without_leaking(setup):
    fleet_env, fake, _ = setup
    bad = ra.Router(fake.url, "wrong-password-value")
    with pytest.raises(ra.RouterError) as err:
        bad.login()
    assert "wrong-password" not in str(err.value)


def test_cloudflare_needs_both_secrets_and_flags_a_restart(setup):
    fleet_env, fake, router = setup
    approve("cloudflare-ai")
    secrets.set_value("CLOUDFLARE_AI_API_TOKEN", "t" * 40)
    assert run(router, fleet_env)["providers"] == {}                 # account id missing
    secrets.set_value("CLOUDFLARE_ACCOUNT_ID", "a" * 32)
    report = run(router, fleet_env)
    assert report["providers"] == {"cloudflare-ai": "created"} and report["needs_restart"] is True


# ------------------------------------------------------------------ the test combo for the mock model
def test_the_test_combo_is_off_by_default_and_only_the_fleet_combos_and_fleet_dev_exist(setup):
    fleet_env, fake, router = setup
    report = run(router, fleet_env)
    assert report["mock"] == "off" and fake.nodes == []
    assert {c["name"] for c in fake.combos} == {"fleet-smart", "fleet-worker", "fleet-coder", "fleet-local", "fleet-dev"}


def test_the_mock_provider_node_and_combo_exist_only_while_the_feature_is_on(setup):
    fleet_env, fake, router = setup
    config.set_override("features.mock_llm", "true")
    report = run(router, fleet_env)
    assert report["mock"] == "created"
    assert [n["prefix"] for n in fake.nodes] == ["mock"] and fake.nodes[0]["baseUrl"] == "http://fleet-mock:20129/v1"
    test = next(c for c in fake.combos if c["name"] == "fleet-test")
    assert [m["model"] for m in test["models"]] == ["mock/fleet-mock"]
    assert run(router, fleet_env)["mock"] == "kept"
    config.set_override("features.mock_llm", "false")
    assert run(router, fleet_env)["mock"] == "removed combo+node"
    assert fake.nodes == [] and "fleet-test" not in {c["name"] for c in fake.combos}


def test_only_the_hermes_key_may_call_the_test_combo_and_only_in_mock_mode(setup):
    fleet_env, fake, router = setup
    config.set_override("features.mock_llm", "true")
    run(router, fleet_env)
    perms = {k["name"]: k["perm"].get("allowedCombos") for k in fake.keys}
    assert "fleet-test" in perms["fleet-hermes"] and "fleet-test" not in perms["fleet-briefing"]
    config.set_override("features.mock_llm", "false")
    run(router, fleet_env)
    assert all("fleet-test" not in (k["perm"].get("allowedCombos") or []) for k in fake.keys)


def test_a_failing_provider_node_is_reported_not_raised(setup, monkeypatch):
    fleet_env, fake, router = setup
    config.set_override("features.mock_llm", "true")
    real = router.req

    def req(method, path, body=None, bearer=None, timeout=30):
        if method == "POST" and path == "/api/provider-nodes":
            return 400, {"error": "bad"}, None
        return real(method, path, body, bearer, timeout)
    monkeypatch.setattr(router, "req", req)
    assert run(router, fleet_env)["mock"].startswith("failed to make the provider node")


NVIDIA = "nvapi-" + "A1b2C3d4E5" * 4


def test_noncommercial_provider_needs_the_owner_opt_in(setup):
    fleet_env, fake, router = setup
    secrets.set_value("NVIDIA_API_KEY", NVIDIA)
    approve("nvidia")
    assert run(router, fleet_env)["providers"] == {}                       # approved and keyed, but no opt-in
    config.set_override("router.allow_noncommercial", "true")
    assert run(router, fleet_env)["providers"] == {"nvidia": "created"}
    config.set_override("router.allow_noncommercial", "false")
    assert run(router, fleet_env)["providers"] == {"nvidia": "removed"}     # turning it off removes the connection


# ------------------------------------------------------------------ fleet-dev: trial models for tests only (D-031)
TRIAL_MODELS = {
    "nvidia": ["nvidia/nemotron-3-ultra-550b-a55b", "google/gemma-4-31b-it", "nvidia/nemotron-3.5-lightning-30b-a3b", "poolside/laguna-xs-2.1"],
    "cohere": ["command-a-03-2025", "command-r-plus-08-2024", "command-r-08-2024"],
}


def test_trial_models_reach_only_fleet_dev_and_only_the_dev_key_can_call_it(fleet_env):
    secrets.set_value("INITIAL_PASSWORD", "pw-test-123-abcdefgh", enforce_format=False)
    config.set_override("router.approved_providers", "[groq, nvidia, cohere]")
    config.set_override("router.allow_noncommercial", "true")
    secrets.set_value("GROQ_API_KEY", GROQ)
    secrets.set_value("NVIDIA_API_KEY", "nvapi-" + "a" * 40, enforce_format=False)
    secrets.set_value("COHERE_API_KEY", "c" * 40, enforce_format=False)
    models = {"groq": ["llama-3.3-70b-versatile", "openai/gpt-oss-20b", "openai/gpt-oss-120b", "qwen/qwen3-32b"], **TRIAL_MODELS}
    fake = FakeOmniRoute(password="pw-test-123-abcdefgh", provider_models=models)
    with fake:
        router = ra.Router(fake.url, "pw-test-123-abcdefgh")
        report = run(router, fleet_env)
        assert report["providers"] == {"groq": "created", "nvidia": "created", "cohere": "created"}
        for name in ("fleet-smart", "fleet-worker", "fleet-coder", "fleet-local"):
            targets = [m["model"] for m in next(c for c in fake.combos if c["name"] == name)["models"]]
            assert not [t for t in targets if t.startswith(("nvidia/", "cohere/"))], (name, targets)
        dev = [m["model"] for m in next(c for c in fake.combos if c["name"] == "fleet-dev")["models"]]
        assert dev and all(t.startswith(("nvidia/", "cohere/")) for t in dev)
        perms = {k["name"]: k["perm"]["allowedCombos"] for k in fake.keys}
        assert "fleet-dev" not in perms["fleet-hermes"] and "fleet-dev" not in perms["fleet-briefing"]
        assert perms["fleet-dev"] == ["fleet-dev"]
        dev_key = secrets.value_of("ROUTER_KEY_DEV")
        assert dev_key.startswith("sk-fake-") and dev_key not in json.dumps(report)


def test_apply_stops_before_any_change_when_a_fleet_client_may_call_fleet_dev(setup, monkeypatch):
    fleet_env, fake, router = setup
    bad = dict(ra.CLIENTS)
    bad["hermes"] = ("ROUTER_KEY_HERMES", ["fleet-smart", "fleet-dev"])
    monkeypatch.setattr(ra, "CLIENTS", bad)
    with pytest.raises(ra.RouterError, match="dev_only"):
        run(router, fleet_env)
    assert fake.combos == [] and fake.keys == []            # nothing reached the router


# ------------------------------------------------------------------ OpenRouter free models (D-037)
def test_openrouter_free_list_keeps_only_free_chat_models():
    from ops.fleetlib import httpc
    body = {"data": [
        {"id": "a/b:free", "pricing": {"prompt": "0", "completion": "0"}},
        {"id": "c/d", "pricing": {"prompt": "0", "completion": "0"}},                      # free but no :free suffix
        {"id": "e/f:free", "pricing": {"prompt": "0.000001", "completion": "0"}},          # the name says free, the price does not
        {"id": "g/h:free", "pricing": {}},                                                  # no price means not proven free
    ]}
    request = lambda method, url, **kw: httpc.Response(200, {}, json.dumps(body).encode())
    assert ra.openrouter_free_models(request) == {"a/b:free"}


def test_openrouter_free_list_is_none_when_it_cannot_be_read():
    from ops.fleetlib import httpc

    def down(method, url, **kw):
        raise httpc.TransportError("no route")
    assert ra.openrouter_free_models(down) is None
    assert ra.openrouter_free_models(lambda m, u, **kw: httpc.Response(503, {}, b"{}")) is None
    assert ra.openrouter_free_models(lambda m, u, **kw: httpc.Response(200, {}, b"not json")) is None
    assert ra.openrouter_free_models(lambda m, u, **kw: httpc.Response(200, {}, b'{"data": []}')) is None


def test_openrouter_targets_are_checked_against_the_public_free_list(setup):
    fleet_env, fake, router = setup
    approve("openrouter")
    secrets.set_value("OPENROUTER_API_KEY", "sk-or-v1-" + "a" * 40, enforce_format=False)
    free = {"nvidia/nemotron-3-ultra-550b-a55b:free", "google/gemma-4-31b-it:free", "nvidia/nemotron-3.5-lightning:free"}
    report = run(router, fleet_env, openrouter_models=lambda: free)
    smart = [m["model"] for m in next(c for c in fake.combos if c["name"] == "fleet-smart")["models"]]
    assert smart == ["openrouter/nvidia/nemotron-3-ultra-550b-a55b:free", "openrouter/google/gemma-4-31b-it:free", "openrouter/nvidia/nemotron-3.5-lightning:free"]
    worker = [m["model"] for m in next(c for c in fake.combos if c["name"] == "fleet-worker")["models"]]
    # gemma-4-26b-a4b-it:free and ling-3.2-flash:free are not in the public list, so they are dropped
    assert worker == ["openrouter/nvidia/nemotron-3.5-lightning:free"]
    assert report["warnings"] == []


def test_openrouter_targets_stay_unchecked_when_the_public_list_is_down(setup):
    fleet_env, fake, router = setup
    approve("openrouter")
    secrets.set_value("OPENROUTER_API_KEY", "sk-or-v1-" + "a" * 40, enforce_format=False)
    report = run(router, fleet_env, openrouter_models=lambda: None)
    smart = [m["model"] for m in next(c for c in fake.combos if c["name"] == "fleet-smart")["models"]]
    assert "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free" in smart and "openrouter/google/gemma-4-31b-it:free" in smart
    assert any("openrouter" in w for w in report["warnings"])


def test_only_free_openrouter_models_ever_reach_a_combo(setup):
    fleet_env, fake, router = setup
    approve("openrouter")
    secrets.set_value("OPENROUTER_API_KEY", "sk-or-v1-" + "a" * 40, enforce_format=False)
    run(router, fleet_env, openrouter_models=lambda: None)
    for combo in fake.combos:
        for m in combo["models"]:
            if m["model"].startswith("openrouter/"):
                assert m["model"].endswith(":free"), m["model"]


# ------------------------------------------------------------------ LLM7, Aion, and Gonka: custom nodes, free only (D-069)
LLM7_KEY = "llm7-" + "A1b2C3d4E5" * 3


def test_llm7_is_a_custom_node_with_a_connection_and_late_fallback_targets(setup):
    fleet_env, fake, router = setup
    approve("llm7")
    secrets.set_value("LLM7_API_KEY", LLM7_KEY)
    report = run(router, fleet_env)
    assert report["providers"] == {"llm7": "created"}
    assert [n["prefix"] for n in fake.nodes] == ["llm7"] and fake.providers[0]["provider"] == fake.nodes[0]["id"]
    worker = next(c for c in fake.combos if c["name"] == "fleet-worker")
    assert any(m["model"].startswith("llm7/") for m in worker["models"])
    assert run(router, fleet_env)["providers"] == {"llm7": "kept"} and len(fake.nodes) == 1       # a second run makes nothing new


def test_a_node_provider_without_approval_or_key_makes_no_node(setup):
    fleet_env, fake, router = setup
    secrets.set_value("LLM7_API_KEY", LLM7_KEY)
    assert run(router, fleet_env)["providers"] == {} and fake.nodes == []        # not approved
    approve("llm7")
    secrets.set_value("LLM7_API_KEY", "x" * 20)
    config.set_override("router.approved_providers", "[aion]")
    assert run(router, fleet_env)["providers"] == {}                              # approved aion has no key


def test_losing_approval_removes_the_connection_of_a_node_provider(setup):
    fleet_env, fake, router = setup
    approve("llm7")
    secrets.set_value("LLM7_API_KEY", LLM7_KEY)
    run(router, fleet_env)
    config.set_override("router.approved_providers", "[]")
    assert run(router, fleet_env)["providers"] == {"llm7": "removed"} and fake.providers == []


def test_every_new_target_is_free_by_the_rule_of_the_code():
    from ops.router import freeonly
    assert freeonly.is_free("llm7/gpt-oss:20b") and freeonly.is_free("aion/aion-labs/aion-3.5-mini")
    assert freeonly.is_free("gonka/zai-org/GLM-5.3-Flash")        # a grant provider. The owner holds no balance. See D-072.
    assert not freeonly.is_free("llm7/gpt-5.4")                  # a pro model needs a paid balance


def test_gonka_is_a_custom_node_that_needs_a_key_and_an_approval(setup):
    """Gonka joins the router as LLM7 did. Its host is the one URL that the locked policy test allows. See D-072."""
    fleet_env, fake, router = setup
    secrets.set_value("GONKA_API_KEY", "gonka-" + "A1b2C3d4E5" * 3)
    assert run(router, fleet_env)["providers"] == {} and fake.nodes == []             # a key without approval does nothing
    approve("gonka")
    assert run(router, fleet_env)["providers"] == {"gonka": "created"}
    assert [n["prefix"] for n in fake.nodes] == ["gonka"] and fake.nodes[0]["baseUrl"] == "https://proxy.gonkabroker.com/v1"
    worker = next(c for c in fake.combos if c["name"] == "fleet-worker")
    assert any(m["model"].startswith("gonka/") for m in worker["models"])
