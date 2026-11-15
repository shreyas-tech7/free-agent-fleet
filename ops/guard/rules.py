"""Rule-based injection checks. They are fast and need no model.

They run in front of the model, and they are the whole screen when the model cannot load.
Each rule has a name. The names go in the answer. The matched text never does.
Score 1.0 means a certain hit. Score 0.5 means a weak signal that does not block alone.
"""
import re
import unicodedata

HIDDEN_CHARS = re.compile("[​-‏‪-‮⁠-⁤⁦-⁩﻿]|[\U000e0000-\U000e007f]")

_VERBS = r"(?:ignore|disregard|forget|override|bypass|skip|drop|discard|neglect)"
_TARGET = r"(?:all |any |the |your |my |these |those |previous |prior |above |earlier |preceding |original |system |safety |security )*"
_THING = r"(?:instructions?|rules?|prompts?|guidelines?|directives?|polic(?:y|ies)|restrictions?|constraints?|guardrails?|context|messages?)"

RULES = [
    ("override_instructions", 1.0, re.compile(r"\b%s\s+%s%s\b" % (_VERBS, _TARGET, _THING), re.I)),
    ("new_instructions", 1.0, re.compile(r"\b(?:new|updated|real|actual|secret|hidden)\s+(?:instructions?|orders?|task|rules?)\s*[:\-]", re.I)),
    ("role_hijack", 1.0, re.compile(r"\b(?:you are now (?:an? |the |in |my |free |unrestricted |called |named |acting |roleplaying |pretending |dan\b|jailbroken|evil|no longer)|from now on,? you|pretend (?:to be|you are)|act as if you (?:have|are|were)|you must now|your new (?:role|task|goal|job))", re.I)),
    ("jailbreak_words", 1.0, re.compile(r"\b(?:developer mode|jailbreak|do anything now|DAN mode|unrestricted mode|god mode|sudo mode)\b", re.I)),
    ("prompt_exfiltration", 1.0, re.compile(r"\b(?:reveal|show|print|repeat|output|leak|display|tell me|give me|dump|echo)\b.{0,40}\b(?:system|initial|hidden|original|secret|full)\s+(?:prompt|instructions?|message|rules?)\b", re.I | re.S)),
    ("role_markup", 1.0, re.compile(r"(?:<\|(?:im_start|im_end|system|user|assistant|endoftext)\|>|\[/?INST\]|<<\s*/?SYS\s*>>|<\s*/?system\s*>|^\s*#{1,3}\s*system\b|\n\s*(?:Human|Assistant|System)\s*:\s)", re.I | re.M)),
    ("ai_addressed", 1.0, re.compile(r"\b(?:attention|note|message|instructions?|important)s?\s*(?:to|for)\s+(?:the\s+)?(?:ai|a\.i\.|assistant|llm|language model|model|agent|bot|chatbot)\b", re.I)),
    ("hidden_comment_instruction", 1.0, re.compile(r"<!--.{0,300}?\b(?:ignore|instruction|assistant|ai agent|system prompt|you must)\b.{0,300}?-->", re.I | re.S)),
    ("exfiltrate_secrets", 1.0, re.compile(r"\b(?:send|post|email|upload|forward|share|paste|print|give|reveal|leak|exfiltrate|transmit)\b.{0,60}(?:\b(?:api[ _-]?keys?|passwords?|tokens?|secrets?|credentials?|private keys?|ssh keys?)\b|\.env\b)", re.I | re.S)),
    ("command_coercion", 1.0, re.compile(r"\b(?:curl|wget)\b[^\n|]{0,200}\|\s*(?:sudo\s+)?(?:ba)?sh\b|\brm\s+-rf\s+/|\b(?:run|execute|call|invoke)\s+(?:the\s+)?(?:following\s+)?(?:shell\s+)?(?:command|script|tool|function)s?\s*[:\-]", re.I)),
    ("tool_instruction", 1.0, re.compile(r"\b(?:use|call|invoke|run)\s+(?:the\s+)?(?:terminal|shell|browser|publisher|deployer)\s+(?:tool\s+)?to\b", re.I)),
    ("encoded_blob", 0.5, re.compile(r"(?:[A-Za-z0-9+/]{200,}={0,2}|(?:[0-9a-fA-F]{2}){100,})")),
]


def normalize(text):
    """NFKC makes fullwidth and lookalike letters plain. It defeats simple obfuscation."""
    return unicodedata.normalize("NFKC", text or "")


def scan(text, max_chars=200000):
    """Return (score, reasons). Reasons are rule names."""
    text = (text or "")[:max_chars]
    reasons = []
    score = 0.0
    if HIDDEN_CHARS.search(text):
        reasons.append("hidden_unicode")
        score = max(score, 1.0)
    plain = normalize(HIDDEN_CHARS.sub("", text))
    for name, weight, rx in RULES:
        if rx.search(plain):
            reasons.append(name)
            score = max(score, weight)
    return score, reasons
