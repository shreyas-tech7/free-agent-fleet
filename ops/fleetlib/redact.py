"""Remove secrets from text before it reaches a log, a prompt, or a post."""
import re

_PATTERNS = [
    r"gsk_[A-Za-z0-9]{20,}",                       # Groq
    r"csk-[A-Za-z0-9]{20,}",                       # Cerebras
    r"sk-or-v1-[A-Za-z0-9]{20,}",                  # OpenRouter
    r"sk-[A-Za-z0-9_\-]{20,}",                     # OpenAI style
    r"AIza[0-9A-Za-z_\-]{30,}",                    # Google
    r"nvapi-[A-Za-z0-9_\-]{20,}",                  # NVIDIA
    r"hf_[A-Za-z0-9]{20,}",                        # Hugging Face
    r"gh[pousr]_[A-Za-z0-9]{30,}",                 # GitHub tokens
    r"github_pat_[A-Za-z0-9_]{30,}",               # GitHub fine-grained
    r"xox[baprs]-[A-Za-z0-9\-]{10,}",              # Slack
    r"[0-9]{6,12}:[A-Za-z0-9_\-]{30,}",            # Telegram bot token
    r"[A-Za-z0-9_\-]{24,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{27,}",  # Discord bot token or JWT
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
]
_TOKEN_RE = re.compile("|".join("(?:%s)" % p for p in _PATTERNS))
_BEARER_RE = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-]{12,}")
_ASSIGN_RE = re.compile(
    r"(?i)\b([A-Za-z0-9_]*(?:api[_-]?key|token|secret|password|passwd|app[_-]?password)[A-Za-z0-9_]*)"
    r"([\"']?\s*[:=]\s*)(['\"]?)([^\s'\",;]{6,})\3"
)

_exact = set()


def register_secret(value):
    """Mask this exact value everywhere from now on (the process remembers it)."""
    if value and len(value) >= 6:
        _exact.add(value)


def redact_text(text):
    if not isinstance(text, str) or not text:
        return text
    for value in sorted(_exact, key=len, reverse=True):
        text = text.replace(value, "[REDACTED]")
    text = _TOKEN_RE.sub("[REDACTED]", text)
    text = _BEARER_RE.sub(lambda m: m.group(1) + " [REDACTED]", text)
    text = _ASSIGN_RE.sub(lambda m: m.group(1) + m.group(2) + "[REDACTED]", text)
    return text


def redact_obj(obj):
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v) for v in obj]
    return obj
