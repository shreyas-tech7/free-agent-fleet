"""Deny lists for the policy tests and the router checks. Part of the locked core."""

# Providers that work only through a subscription login or an OAuth login (rule R3 and R4).
SUBSCRIPTION_PROVIDERS = (
    "claude-code", "claude_code", "claudecode", "claude-oauth", "anthropic-oauth",
    "codex", "chatgpt", "openai-codex", "antigravity", "gemini-cli", "gemini_cli",
    "qoder", "kiro", "qwen-oauth", "qwen_oauth", "copilot", "github-copilot", "cursor",
    "minimax-oauth", "xai-oauth", "nous", "windsurf", "cline-oauth",
)

# Words in router config that mean a forbidden feature (rule R4).
FORBIDDEN_ROUTER_TERMS = (
    "oauth", "subscription", "stealth", "tls-fingerprint", "tls_fingerprint", "utls", "ja3",
    "proxy", "socks", "vpn", "geo-bypass", "region-bypass", "region_bypass", "geo_bypass",
    "cookie", "session_token", "browser_session",
)

# Endpoints of paid-only APIs (rule R1). A config must not point at them.
PAID_ENDPOINTS = (
    "api.openai.com", "api.anthropic.com", "api.x.ai", "api.perplexity.ai", "openai.azure.com",
    "bedrock-runtime", "api.together.xyz", "api.fireworks.ai", "api.deepseek.com", "api.replicate.com",
    "api.mistral.ai/v1/fim",
)

# Settings that mean money. Fleet config must not hold them (rule R1).
BILLING_KEYS = (
    "billing", "payment_method", "credit_card", "card_number", "stripe_secret", "paypal",
    "invoice", "purchase", "upgrade_plan",
)

PAID_PLAN_WORDS = ("pro", "paid", "team", "business", "enterprise", "premium", "plus", "unlimited")
