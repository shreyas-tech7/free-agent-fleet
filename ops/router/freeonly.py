"""The $0 rule in code. See D-047 and rule R1.

A combo target is free only when its provider has a free tier that needs no card, and its model is not a paid variant.
Everything else is refused. `apply` drops a refused target and says so. A policy test checks every target in config/router/combos.yaml.
"""
FREE_TIER_PROVIDERS = frozenset({"gemini", "groq", "cloudflare-ai", "openrouter", "nvidia", "cohere", "ollama", "llm7", "aion", "gonka"})
# LLM7 gives free tokens for its `turbo` tier only. Its `pro` tier needs a subscription or a paid balance. See D-069.
LLM7_TURBO = frozenset({"DeepSeek-V4-Flash-0731", "codestral-latest", "glm-5.3-flash", "gpt-oss:20b", "minimax-m2.7", "mistral-Nemo-Instruct-2407"})
# Aion and Gonka price every model per token. Their free access is an account grant, and no balance means no charge.
# The owner confirmed that neither account holds a card or a balance, and must never add money. `fleetctl secret check` reports a 402 as "no free credit". See D-069 and D-072.
# OpenRouter route suffixes that bill the request. Only ":free" is free.
PAID_SUFFIXES = (":nitro", ":floor", ":online", ":extended", ":thinking", ":beta")


class PaidModelError(ValueError):
    pass


def split(target):
    provider, _, model = str(target).partition("/")
    return provider, model


def refusal(target):
    """Return why the target is refused, or None when it is free."""
    provider, model = split(target)
    if not provider or not model:
        return "the target is not provider/model"
    if provider not in FREE_TIER_PROVIDERS:
        return "provider %s is not on the free-tier list" % provider
    if provider == "openrouter" and not model.endswith(":free"):
        return "an OpenRouter model must end with :free"
    if provider == "llm7" and model not in LLM7_TURBO:
        return "an LLM7 model must be on the free turbo list"
    if any(model.endswith(suffix) for suffix in PAID_SUFFIXES):
        return "the route suffix bills the request"
    return None


def is_free(target):
    return refusal(target) is None


def check_targets(targets):
    """Return [(target, reason)] for each target that is refused."""
    return [(t, refusal(t)) for t in targets if refusal(t)]


def check_combos(combos_cfg):
    """Return [(combo, target, reason)] for every refused target in a combos file."""
    bad = []
    for name, combo in (combos_cfg.get("combos") or {}).items():
        for target, reason in check_targets(combo.get("targets") or []):
            bad.append((name, target, reason))
    return bad


def assert_free(targets):
    bad = check_targets(targets)
    if bad:
        raise PaidModelError("; ".join("%s: %s" % b for b in bad))
