"""Policy: the $0 rule in code. Every combo target is free. A paid target is refused. See D-047 and rule R1."""
import pathlib

import pytest
import yaml

from ops.router import apply as router_apply
from ops.router import freeonly

ROOT = pathlib.Path(__file__).resolve().parents[3]
COMBOS = yaml.safe_load((ROOT / "config" / "router" / "combos.yaml").read_text())


def test_every_target_in_the_combos_file_is_free():
    assert freeonly.check_combos(COMBOS) == []


@pytest.mark.parametrize("target", ["gemini/gemini-2.5-flash", "groq/openai/gpt-oss-20b", "cloudflare-ai/@cf/meta/llama-3.3-70b-instruct-fp8-fast",
                                    "openrouter/google/gemma-4-31b-it:free", "ollama/qwen3:1.7b", "nvidia/nvidia/nemotron-3-ultra-550b-a55b"])
def test_free_targets_pass(target):
    assert freeonly.is_free(target) and freeonly.refusal(target) is None


@pytest.mark.parametrize("target,why", [
    ("openai/gpt-5", "not on the free-tier list"),
    ("anthropic/claude-opus-5-5", "not on the free-tier list"),
    ("openrouter/openai/gpt-5", "must end with :free"),
    ("openrouter/google/gemma-4-31b-it", "must end with :free"),
    ("openrouter/google/gemma-4-31b-it:nitro", "must end with :free"),
    ("groq/openai/gpt-oss-20b:nitro", "route suffix"),
    ("mistral/large", "not on the free-tier list"),
    ("gemini", "not provider/model"),
    ("", "not provider/model"),
])
def test_paid_or_odd_targets_are_refused(target, why):
    assert why in freeonly.refusal(target)
    assert not freeonly.is_free(target)


def test_check_combos_names_the_combo_and_the_target():
    bad = freeonly.check_combos({"combos": {"fleet-x": {"targets": ["groq/openai/gpt-oss-20b", "openai/gpt-5"]}}})
    assert bad == [("fleet-x", "openai/gpt-5", "provider openai is not on the free-tier list")]


def test_assert_free_raises_with_every_refusal():
    freeonly.assert_free(["groq/openai/gpt-oss-20b"])
    with pytest.raises(freeonly.PaidModelError) as err:
        freeonly.assert_free(["openai/gpt-5", "openrouter/x/y"])
    assert "openai/gpt-5" in str(err.value) and "openrouter/x/y" in str(err.value)


def test_apply_never_keeps_a_paid_target_even_when_the_provider_is_connected():
    combo = {"targets": ["openai/gpt-5", "openrouter/openai/gpt-5", "openrouter/google/gemma-4-31b-it:free", "groq/openai/gpt-oss-20b"]}
    kept = router_apply.select_targets(combo, {"openai", "openrouter", "groq"}, {})
    assert kept == ["openrouter/google/gemma-4-31b-it:free", "groq/openai/gpt-oss-20b"]
