"""Policy: the router config has no subscription, OAuth, stealth, proxy, or `tos: avoid` provider."""
import pathlib

import yaml

from core.policy import denylists as dl
from ops.router import apply as ra
from ops.router import litellm_gen

ROOT = pathlib.Path(__file__).resolve().parents[3]
COMBOS = ROOT / "config" / "router" / "combos.yaml"
ALLOWED_PROVIDERS = {"groq", "cloudflare-ai", "gemini", "openrouter", "nvidia", "cohere", "ollama"}


def router_files():
    return [p for p in (ROOT / "config" / "router").glob("*") if p.is_file()]


def walk(node):
    if isinstance(node, dict):
        for k, v in node.items():
            yield str(k)
            yield from walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from walk(v)
    elif node is not None:
        yield str(node)


def test_router_files_exist():
    names = {p.name for p in router_files()}
    assert {"combos.yaml", "litellm.yaml"} <= names


def test_no_forbidden_router_terms_anywhere():
    bad = []
    for path in router_files():
        doc = yaml.safe_load(path.read_text())
        # Check keys and values. Comments are not data.
        for token in walk(doc):
            low = token.lower()
            for term in dl.FORBIDDEN_ROUTER_TERMS:
                if term in low:
                    bad.append("%s: %r contains %r" % (path.name, token, term))
    assert not bad, bad


def test_no_subscription_provider_is_named():
    doc = yaml.safe_load(COMBOS.read_text())
    names = set(doc["providers"]) | {t.split("/")[0] for c in doc["combos"].values() for t in c["targets"]}
    assert not (names & set(dl.SUBSCRIPTION_PROVIDERS))
    assert names <= ALLOWED_PROVIDERS, names - ALLOWED_PROVIDERS


def find_tos_avoid(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            if str(k).lower() in ("tos", "tos_verdict") and str(v).lower() == "avoid":
                yield path + "/" + str(k)
            yield from find_tos_avoid(v, path + "/" + str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from find_tos_avoid(v, "%s[%d]" % (path, i))


def test_no_tos_avoid_marker_and_every_plan_is_free():
    bad = []
    for path in router_files():
        bad += ["%s %s" % (path.name, w) for w in find_tos_avoid(yaml.safe_load(path.read_text()))]
    assert not bad, bad
    doc = yaml.safe_load(COMBOS.read_text())
    assert all(p.get("plan") == "free" for p in doc["providers"].values())


def test_tos_avoid_detector_works():
    assert list(find_tos_avoid({"providers": {"x": {"tos": "avoid"}}}))
    assert not list(find_tos_avoid({"providers": {"x": {"tos": "ok"}}}))


def test_the_four_fleet_combos_and_fleet_dev_exist_and_use_known_providers():
    doc = yaml.safe_load(COMBOS.read_text())
    assert set(doc["combos"]) == {"fleet-smart", "fleet-worker", "fleet-coder", "fleet-local", "fleet-dev"}
    for name, combo in doc["combos"].items():
        assert combo["targets"], name
        for target in combo["targets"]:
            provider = target.split("/")[0]
            assert provider in doc["providers"] or provider == "ollama", target
            if provider == "openrouter":
                assert target.endswith(":free"), "OpenRouter targets must be free models: " + target
    assert doc["combos"]["fleet-local"].get("local_only") is True
    assert all(t.startswith("ollama/") for t in doc["combos"]["fleet-local"]["targets"])


def test_router_settings_turn_on_zero_cost_and_turn_off_proxy():
    s = ra.POLICY_SETTINGS
    assert s["freeAccessPolicy"] == "strict" and s["excludeTosAvoid"] is True and s["hidePaidModels"] is True
    assert s["proxyEnabled"] is False and s["cloudEnabled"] is False


def test_client_keys_can_only_call_fleet_combos():
    for _label, (_secret, allowed) in ra.CLIENTS.items():
        assert allowed and all(name.startswith("fleet-") for name in allowed)


def test_litellm_fallback_has_the_same_names_and_is_in_sync():
    import yaml as y
    fallback = y.safe_load((ROOT / "config" / "router" / "litellm.yaml").read_text())
    names = {m["model_name"] for m in fallback["model_list"]}
    assert names == {"fleet-smart", "fleet-worker", "fleet-coder", "fleet-local"}
    assert litellm_gen.OUT.read_text() == litellm_gen.build()
    assert fallback["litellm_settings"]["telemetry"] is False


def test_router_secrets_in_the_registry_match_the_provider_list():
    reg = yaml.safe_load((ROOT / "config" / "secrets.yaml").read_text())
    router_secrets = {s["name"] for s in reg["secrets"] if s["group"] == "router"}
    combos = yaml.safe_load(COMBOS.read_text())
    wanted = {name for p in combos["providers"].values() for name in p["secrets"]}
    assert router_secrets == wanted


def test_the_test_combo_points_only_to_a_local_mock_and_has_no_cloud_target():
    doc = yaml.safe_load((ROOT / "config" / "router" / "test-combo.yaml").read_text())
    assert doc["combo"]["name"] == "fleet-test" and doc["combo"]["targets"] == ["mock/fleet-mock"]
    assert doc["provider"]["prefix"] == "mock" and doc["provider"]["base_url"].startswith("http://fleet-mock:")
    assert "mock_llm" in (ROOT / "ops" / "router" / "apply.py").read_text()      # the apply step makes it only in mock mode
