"""House style for every text that the owner reads. See skills/house-style/SKILL.md and D-046.

check(text) returns a list of problems. fix(text) repairs the mechanical ones: dashes and semicolons.
The briefing and the card reports use both. A text that still has a problem after fix() is replaced by the plain-code version.
Code blocks, inline code, and web addresses are not checked.
"""
import re

MAX_WORDS = 32
BANNED = [
    (re.compile(r"\bit is important to note\b", re.I), "it is important to note"),
    (re.compile(r"\btestaments?\b", re.I), "testament"),
    (re.compile(r"\bnot just\b[^.!?]{0,80}?\bbut also\b", re.I), "not just ... but also"),
    (re.compile(r"\bpivotal\b", re.I), "pivotal"),
    (re.compile(r"\bseamless(?:ly)?\b", re.I), "seamless"),
    (re.compile(r"\bdelv(?:e|es|ed|ing)\b", re.I), "delve"),
]
IRREGULAR = "built|made|done|sent|found|kept|held|run|set|put|left|brought|caught|taught|bought|sold|told|paid|read|led|lost|met|won|shown|known|grown|drawn|thrown"
PASSIVE = re.compile(r"\b(?:is|are|was|were|been|being)\s+(?:\w+ly\s+)?(?:\w+(?:ed|en)|%s)\s+by\b" % IRREGULAR, re.I)
FENCE = re.compile(r"```.*?```", re.S)
INLINE = re.compile(r"`[^`\n]*`")
URL = re.compile(r"https?://\S+")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _prose(text):
    """The text without code and web addresses."""
    text = FENCE.sub(" ", str(text))
    text = INLINE.sub(" ", text)
    return URL.sub(" ", text)


def check(text):
    """Return the problems in a text, as short phrases. An empty list means that the text follows the house style."""
    prose = _prose(text)
    found = []
    if "—" in prose or "–" in prose:
        found.append("dash")
    if ";" in prose:
        found.append("semicolon")
    for pattern, name in BANNED:
        if pattern.search(prose):
            found.append("banned phrase: " + name)
    match = PASSIVE.search(prose)
    if match:
        found.append("passive voice: " + match.group(0).lower())
    for line in prose.splitlines():
        for sentence in SENTENCE_END.split(line.strip()):
            words = len(sentence.split())
            if words > MAX_WORDS:
                found.append("sentence of %d words" % words)
    return found


def fix(text):
    """Repair dashes and semicolons. Other problems need a rewrite. Code stays as it is."""
    parts = []
    last = 0
    for match in sorted(list(FENCE.finditer(text)) + list(INLINE.finditer(text)) + list(URL.finditer(text)), key=lambda m: m.start()):
        if match.start() < last:
            continue
        parts.append((True, text[last:match.start()]))
        parts.append((False, match.group(0)))
        last = match.end()
    parts.append((True, text[last:]))
    out = []
    for is_prose, chunk in parts:
        if is_prose:
            chunk = re.sub(r"(?<=\d)–(?=\d)", "-", chunk)
            chunk = re.sub(r"\s*[—–]\s*", ", ", chunk)
            chunk = re.sub(r";\s*(\w)", lambda m: ". " + m.group(1).upper(), chunk)
            chunk = re.sub(r";\s*$", ".", chunk)
            chunk = chunk.replace(";", ".")
        out.append(chunk)
    return "".join(out)


def clean(text):
    """Return (text, problems). The text is fixed. The problems are what is left."""
    fixed = fix(text)
    return fixed, check(fixed)
