"""JSON schemas for the outputs of the agents. The dry run checks the mock answers against them."""

SCORES = ("pain", "proof_of_paying", "free_channel_reach", "buildable_in_two_weeks", "competitor_gaps", "legal_risk")

PAIN_REPORT = {
    "type": "object", "required": ["project", "candidates"],
    "properties": {
        "project": {"type": "string"},
        "candidates": {"type": "array", "minItems": 3, "items": {
            "type": "object", "required": ["id", "pain", "complaints", "paid_workaround", "scores"],
            "properties": {
                "id": {"type": "string"}, "pain": {"type": "string", "minLength": 5}, "complaints": {"type": "integer", "minimum": 0},
                "paid_workaround": {"type": "string", "minLength": 3},
                "scores": {"type": "object", "required": list(SCORES), "additionalProperties": False,
                           "properties": {k: {"type": "integer", "minimum": 1, "maximum": 5} for k in SCORES}},
            }}},
    },
}

LANDING = {
    "type": "object", "required": ["headline", "body", "cta", "faq"],
    "properties": {"headline": {"type": "string", "maxLength": 100}, "body": {"type": "string"}, "cta": {"type": "string", "maxLength": 40},
                   "faq": {"type": "array", "items": {"type": "object", "required": ["id", "question", "answer"]}}},
}

POST = {"type": "object", "required": ["text"], "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 300}}}
FIX_NOTE = {"type": "object", "required": ["summary", "tests_pass"], "properties": {"summary": {"type": "string"}, "tests_pass": {"type": "boolean"}}}
BRIEF = {"type": "object", "required": ["achieved", "next", "limits"], "properties": {k: {"type": "string", "minLength": 1} for k in ("achieved", "next", "limits")}}
