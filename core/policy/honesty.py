"""The honesty filter. It is part of the locked core.

The Publisher runs every public text through `check()` before it posts.
The filter enforces rules 7 and 8 of core/RULES.md:

- No fake reviews, testimonials, users, numbers, or claims.
- No invented staff or founders. Never claim to be human.
- No statement in marketing that the company is run by AI.

Evidence lets a true claim through. The caller passes a list of evidence items that
plain code verified, for example a metric from the project metrics store:
    {"kind": "metric",      "id": "waitlist_signups", "value": 37}
    {"kind": "testimonial", "id": "ticket-123",       "text": "Saved me an hour a week."}
A number or a quote passes only when it matches an evidence item.

This is a rule-based filter. It is one layer. It errs on the side of blocking.
"""
import re
from dataclasses import dataclass, field

MARKETING = "marketing"
CHAT = "chat"
INTERNAL = "internal"


@dataclass
class Violation:
    rule: str
    snippet: str
    why: str


@dataclass
class Verdict:
    allowed: bool = True
    violations: list = field(default_factory=list)

    def add(self, rule, snippet, why):
        self.allowed = False
        self.violations.append(Violation(rule, snippet.strip()[:120], why))

    def rules(self):
        return sorted({v.rule for v in self.violations})


def _norm(text):
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text)


# ---- claims to be human, invented staff and founders --------------------------------------
_HUMAN_CLAIMS = [
    r"\bI(?:'m| am)\s+(?:a |an )?(?:real |actual |living |regular )?(?:human|person|man|woman|guy|girl|dude)\b",
    r"\bI(?:'m| am) not (?:a |an )?(?:bot|robot|ai|machine|chatbot|automated)\b",
    r"\b(?:this is|you(?:'re| are) (?:talking|speaking|chatting) (?:to|with)) (?:a )?(?:real )?(?:human|person)\b",
    r"\breal (?:human|person|people)\s+(?:here|behind|answer|reply|replies|writing|typing)\b",
    r"\b(?:human|person) (?:behind|writing|typing|answering)\b",
    r"\bas (?:a|an) (?:real |actual )?(?:human|person|human being|founder|solo ?founder|indie (?:hacker|maker|developer)|developer|engineer|parent|mom|dad)\b",
    r"\bmy (?:wife|husband|kids?|daughter|son|family|mom|mother|dad|father|boss|girlfriend|boyfriend|partner|co-?founder)\b",
    r"\bI (?:quit|left) my (?:job|career)\b",
    r"\bI(?:'ve| have) been (?:a|an) [a-z\- ]{3,30} for (?:over |more than )?\d+ years\b",
    r"\bI (?:personally|myself) (?:built|wrote|made|designed|coded|tested|founded|started|created)\b",
    r"\bI (?:founded|co-?founded|started|launched|built|created|run|own) (?:this|the|our|my) (?:company|business|startup|product|brand|studio)\b",
    r"\b(?:our|the) (?:founder|co-?founders?|ceo|cto|cfo|coo|head of [a-z]+|lead (?:engineer|designer|developer)|support team|customer success team|team of (?:\d+ )?(?:experts|engineers|developers|specialists|people))\b",
    r"\bmeet (?:the team|our team|[A-Z][a-z]+(?: [A-Z][a-z]+)?,? (?:our|the) )",
    r"\b(?:founder|co-?founder|ceo|cto)\s+(?:and|&)\s+(?:ceo|cto|founder|developer|designer)\b",
    r"\bsent from my (?:iphone|android|phone|laptop)\b",
    r"\bI(?:'m| am) (?:a |an )?(?:solo ?)?(?:founder|developer|engineer|designer|entrepreneur|maker|indie hacker|freelancer)\b",
]
_HUMAN_RE = [re.compile(p, re.I) for p in _HUMAN_CLAIMS]

# ---- the company is run by AI (forbidden in marketing) --------------------------------------
_AI_RUN = [
    r"\b(?:run|runs|ran|operated|operates|managed|manages|owned|founded|built|created|made|written|designed|developed|coded|staffed|powered|driven|led) (?:entirely |fully |completely |autonomously |solely |mostly )?by (?:an? |our |the )?(?:ai|a\.i\.|artificial intelligence|autonomous (?:ai |software )?agents?|ai agents?|bots?|robots?|machines?|llms?|language models?)\b",
    r"\b(?:ai|a\.i\.|autonomous|robot|bot)[- ](?:run|operated|managed|owned|founded|built|led|staffed|driven)\b",
    r"\b(?:ai|a\.i\.|autonomous|robot|bot)[- ](?:company|business|startup|founder|ceo|team|studio|brand)\b",
    r"\b(?:fully|completely|100%|entirely) (?:autonomous|automated|ai)\b",
    r"\bno (?:humans?|people|employees|staff) (?:involved|behind|needed|work here|in the loop)\b",
    r"\b(?:company|business|startup|studio|brand|team|products?) (?:is|are|was|were) (?:run|built|made|created|operated|managed|written|designed) by (?:ai|artificial intelligence|agents|bots|robots|machines)\b",
    r"\b(?:our|the) (?:ai|autonomous|robot|bot) (?:team|employees?|staff|workers?|agents?|founders?|developers?)\b",
    r"\bmade by (?:ai|robots|machines|bots)\b",
    r"\b(?:ai|artificial intelligence) (?:wrote|built|designed|runs|operates|created|made) (?:this|our|the|everything)\b",
]
_AI_RUN_RE = [re.compile(p, re.I) for p in _AI_RUN]

# ---- invented numbers and social proof ----------------------------------------------------
_NUM = r"\d[\d,\.]*(?:\s?[kKmMbB]\b|\+)?"
_NUMBER_CLAIMS = [
    r"(?P<n>%s)\s*(?:\+\s*)?(?:happy |satisfied |active |paying |loyal |daily |monthly |early )?(?:users?|customers?|clients?|teams?|businesses|companies|creators|developers|people|members|subscribers|signups|sign-ups|downloads|installs|reviews|ratings|sales|orders|stars|downloads|visits|visitors)\b" % _NUM,
    r"(?P<n>\d{1,3}(?:\.\d+)?)\s?%\s*(?:of\s+)?(?:satisfaction|faster|more|less|fewer|increase|growth|improvement|reduction|savings?|accuracy|uptime|success|conversion|roi|better|cheaper|time|boost)\b",
    r"(?P<n>\d{1,3}(?:\.\d+)?)\s?%\s+of\s+(?:[A-Za-z\-]+\s+){0,2}(?:users?|customers?|clients?|people|teams?|subscribers?|developers?|creators?|businesses|companies|members|readers|respondents|buyers|participants|founders|freelancers|managers|students|parents|owners|professionals|marketers|designers|engineers|shoppers|visitors)\b",
    r"(?P<n>\d+(?:\.\d+)?)\s?x\s+(?:faster|more|better|growth|roi|productive|cheaper|bigger|results|return|speed)\b",
    r"\brated\s+(?P<n>\d(?:\.\d)?)\s*(?:/|out of)\s*\d\b",
    r"\b(?P<n>\d(?:\.\d)?)\s*(?:-| )?stars?\b",
    r"[\$€£]\s?(?P<n>%s)\s*(?:k|m|million|billion|thousand)?\s*(?:saved|earned|revenue|in sales|in revenue|made|generated|profit|raised|in savings)\b" % _NUM,
    r"\b(?:saved|saves|earned|made|generated|raised)\s+[\$€£]\s?(?P<n>%s)" % _NUM,
    r"\b(?:over|more than|nearly|almost|around|about|up to)\s+(?P<n>%s)\s+(?:users?|customers?|clients?|teams?|people|businesses|downloads|signups|sales|orders)\b" % _NUM,
    r"(?:^|\s)(?:#|number\s*)1\b(?:\s+(?:rated|ranked|choice|tool|app|product|platform))?",
    r"\b(?:best[- ]selling|award[- ]winning|top[- ]rated|industry[- ]leading|market[- ]leading|world[- ]class|most (?:popular|trusted|loved))\b",
    r"\b(?:trusted|loved|used|chosen|recommended|backed) by (?:thousands|millions|hundreds|dozens|leading|top|many|teams|companies|developers|creators|businesses|people)\b",
    r"\b(?:thousands|millions|hundreds) of (?:happy |satisfied )?(?:users|customers|clients|teams|people|businesses|developers|creators)\b",
    r"\bas seen (?:on|in)\b",
    r"\bfeatured (?:on|in) (?:forbes|techcrunch|wired|the new york times|nyt|bbc|cnn|product hunt|hacker news)\b",
    r"\bjoin (?:the )?(?:thousands|millions|hundreds|\d[\d,\.]*\+?)\b",
    r"\b(?:\d[\d,\.]*\+?)\s+(?:people|others|users|teams) (?:are )?(?:already|have)\b",
]
_NUMBER_RE = [re.compile(p, re.I) for p in _NUMBER_CLAIMS]

# ---- fake reviews and testimonials ----------------------------------------------------------
_TESTIMONIAL_PHRASES = [
    r"\bwhat (?:our|my) (?:customers?|users?|clients?|people) (?:are )?(?:say|saying|think)\b",
    r"\b(?:customers?|users?|clients?) (?:say|said|tell us|told us|report|reported|love|loved|raves?|swear by)\b",
    r"\btestimonials?\b",
    r"\bverified (?:buyer|customer|user|purchase|review)\b",
    r"\b(?:five|5)[- ]star (?:review|rating|reviews|ratings)\b",
    r"\b(?:rave|glowing|great|amazing|excellent) reviews?\b",
    r"\bread (?:our |the )?(?:reviews|testimonials|success stories)\b",
    r"\bsuccess stor(?:y|ies)\b",
    r"\bcase stud(?:y|ies)\b",
]
_TESTIMONIAL_PHRASE_RE = [re.compile(p, re.I) for p in _TESTIMONIAL_PHRASES]
_QUOTE_RE = re.compile(r"[\"']([^\"']{12,300})[\"']")
_ATTRIBUTION_RE = re.compile(
    r"[\"'\)]\s*(?:[-~–—]|--)\s*[A-Z][A-Za-z\.]+(?:\s+[A-Z][A-Za-z\.]+){0,3}"
    r"(?:,\s*[A-Za-z ,&]{2,40})?"
)
_FIRST_PERSON_PRAISE_RE = re.compile(
    r"^\s*[\"']?(?:i|we) (?:love|loved|can't live without|couldn't live without|swear by|use this (?:every|daily)|highly recommend|recommend this)\b", re.I)


def _evidence_values(evidence):
    values, quotes = set(), []
    for item in evidence or []:
        if not isinstance(item, dict):
            continue
        if "value" in item:
            values.add(re.sub(r"[,\s\+]", "", str(item["value"])).lower())
        if item.get("kind") == "testimonial" and item.get("text"):
            quotes.append(_norm(str(item["text"])).strip().lower())
    return values, quotes


def _number_backed(token, values):
    cleaned = re.sub(r"[,\s\+]", "", token).lower()
    if cleaned in values:
        return True
    # accept 1.2k for 1200, 12k for 12000
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([km])", cleaned)
    if m:
        scale = 1000 if m.group(2) == "k" else 1000000
        return str(int(float(m.group(1)) * scale)) in values
    return False


def check(text, context=MARKETING, evidence=None):
    """Return a Verdict for the text. context is marketing, chat, or internal."""
    verdict = Verdict()
    if context == INTERNAL:
        return verdict
    t = _norm(text or "")
    if not t.strip():
        return verdict
    values, quotes = _evidence_values(evidence)

    for rx in _HUMAN_RE:
        m = rx.search(t)
        if m:
            verdict.add("claims_human", m.group(0), "Never claim to be human or invent staff, founders, or a personal life.")

    if context == MARKETING:
        for rx in _AI_RUN_RE:
            m = rx.search(t)
            if m:
                verdict.add("ai_run_claim", m.group(0), "Marketing must not say that the company is run by AI.")

    for rx in _NUMBER_RE:
        for m in rx.finditer(t):
            n = m.groupdict().get("n")
            if n is not None and _number_backed(n, values):
                continue
            if n is None and values:
                # a social-proof phrase with no number can pass only if some evidence exists for it
                pass
            verdict.add("invented_number", m.group(0), "A number or social-proof claim needs real evidence that you can name.")

    for rx in _TESTIMONIAL_PHRASE_RE:
        m = rx.search(t)
        if m and not quotes:
            verdict.add("fake_testimonial", m.group(0), "Testimonials and reviews need a real quote as evidence.")
    for q in _QUOTE_RE.finditer(t):
        body = q.group(1).strip().lower()
        attributed = bool(_ATTRIBUTION_RE.search(t[q.start():q.end() + 80]))
        praise = bool(_FIRST_PERSON_PRAISE_RE.match(q.group(0)))
        if (attributed or praise) and not any(body in real or real in body for real in quotes):
            verdict.add("fake_testimonial", q.group(0), "A quoted praise or attributed quote needs a real testimonial as evidence.")

    # one violation per (rule, snippet)
    seen, unique = set(), []
    for v in verdict.violations:
        key = (v.rule, v.snippet.lower())
        if key not in seen:
            seen.add(key)
            unique.append(v)
    verdict.violations = unique
    return verdict


def must_disclose_ai(region):
    """True when the law requires a customer-facing chat to say it is an AI assistant.

    EU AI Act Article 50 applies from 2026-08-02. California SB 1001 applies to bots
    that promote sales to people in California. Other regions: disclose anyway when asked.
    """
    return (region or "").strip().lower() in ("eu", "european union", "ca", "california", "us-ca", "unknown")
