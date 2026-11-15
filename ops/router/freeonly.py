"""The $0 rule in code. See D-047 and rule R1.

A combo target is free only when its provider has a free tier that needs no card, and its model is not a paid variant.
Everything else is refused. `apply` drops a refused target and says so. A policy test checks every target in config/router/combos.yaml.
"""
FREE_TIER_PROVIDERS = frozenset({"gemini", "groq", "cloudflare-ai", "openrouter", "nvidia", "cohere", "ollama"})
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
