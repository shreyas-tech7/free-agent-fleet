"""Policy: the honesty filter blocks fake reviews, fake testimonials, invented numbers,
AI-run claims in marketing, and any claim to be human. It still lets honest copy through."""
import pytest

from core.policy import honesty as h

BLOCK = {
    "claims_human": [
        "I'm a real person, not a bot.", "Hi, I am a human and I read every reply.",
        "As a founder, I know this pain.", "my wife and kids love the app", "I quit my job to build this",
        "I've been a developer for 12 years", "Meet the team: Sarah, our CTO", "our founder started this in a garage",
        "I personally built this tool", "You're talking to a real person here", "I'm not an AI, promise",
        "Sent from my iPhone", "I'm a solo founder",
    ],
    "ai_run_claim": [
        "This company is run by AI.", "Built entirely by AI agents.", "An AI-run business that never sleeps",
        "100% autonomous company", "No humans involved", "Our AI team ships daily", "Founded by an AI",
        "operated autonomously by AI agents", "made by robots", "AI-powered company run by autonomous agents",
    ],
    "invented_number": [
        "Join 10,000+ happy users", "Trusted by thousands of teams", "Rated 4.9/5 by customers",
        "5 stars on every review", "Saved $50,000 for our customers", "3x faster than the competition",
        "97% of users renew", "55% of people save time every week", "83% of small teams ship faster",
        "Over 500 customers already", "Best-selling planner on the web",
        "As seen on TechCrunch", "#1 rated tool", "12k downloads this week",
    ],
    "fake_testimonial": [
        '"This changed my business!" - Jane D.', '"I love this product, five stars" — Mark T., Founder',
        "What our customers are saying", "Read our testimonials", "Verified buyer: great tool",
        "Customers say it saves hours",
    ],
}

ALLOW = [
    "Meal planner for busy families. Plan a week of dinners in 5 minutes.",
    "The free plan includes 3 projects. The pro plan costs $9 per month.",
    "We built a small tool that turns your notes into a checklist. Try it and tell us what is missing.",
    "Step 1: paste your text. Step 2: pick a template. Step 3: copy the result.",
    "Waitlist is open. Sign up and we email you when it is ready.",
    "Our assistant is an AI. It can help you write a first draft.",
    "AI-powered summaries of long documents.",
    "It runs in your browser and stores nothing on a server.",
    "We read every message. Reply to this post with feedback.",
    "Version 1.2 adds dark mode and a CSV export.",
    "Works with 4 file types: txt, md, csv, json.",
    "Open source. The code is on GitHub.",
    "A 7 day trial, no card needed.",
    "Save 20% with code SPRING",
]


@pytest.mark.parametrize("rule,text", [(r, t) for r, items in BLOCK.items() for t in items])
def test_blocks_dishonest_text(rule, text):
    verdict = h.check(text)
    assert not verdict.allowed
    assert rule in verdict.rules(), "%r was blocked for %s, not for %s" % (text, verdict.rules(), rule)


@pytest.mark.parametrize("text", ALLOW)
def test_allows_honest_text(text):
    verdict = h.check(text)
    assert verdict.allowed, [v.snippet for v in verdict.violations]


def test_ai_run_claim_is_a_marketing_rule_only():
    text = "This company is run by AI."
    assert not h.check(text, context=h.MARKETING).allowed
    assert "ai_run_claim" not in h.check(text, context=h.CHAT).rules()


def test_chat_may_say_it_is_an_ai_assistant_but_never_a_human():
    assert h.check("I'm an AI assistant. How can I help?", context=h.CHAT).allowed
    assert not h.check("I'm a real person and I will help you.", context=h.CHAT).allowed


def test_a_number_passes_only_with_matching_evidence():
    text = "37 people joined the waitlist"
    assert not h.check(text).allowed
    assert h.check(text, evidence=[{"kind": "metric", "id": "waitlist_signups", "value": 37}]).allowed
    assert not h.check(text, evidence=[{"kind": "metric", "id": "waitlist_signups", "value": 12}]).allowed
    assert h.check("Over 1,200 customers", evidence=[{"kind": "metric", "id": "c", "value": 1200}]).allowed


def test_a_quote_passes_only_with_a_real_testimonial():
    quote = "Saved me an hour a week."
    text = '"%s" - Alex P.' % quote
    assert not h.check(text).allowed
    ev = [{"kind": "testimonial", "id": "ticket-9", "text": quote}]
    assert "fake_testimonial" not in h.check(text, evidence=ev).rules()
    assert not h.check('"Best tool ever, saved my company." - Alex P.', evidence=ev).allowed


def test_internal_text_is_not_checked():
    assert h.check("I'm a real person", context=h.INTERNAL).allowed


def test_empty_text_is_fine():
    assert h.check("").allowed and h.check(None).allowed


def test_disclosure_rule_for_regions():
    assert h.must_disclose_ai("EU") and h.must_disclose_ai("California")
