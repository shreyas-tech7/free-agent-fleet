"""Employee-style card reports. See skills/house-style/SKILL.md and D-046.

Each finished card ends with four lines: Done, Next, Blocked, and Needs you.
parse() reads them from the closing note of a card. render() writes them for a chat message.
A note that lacks a label is not a report. The caller then shows the plain note.
"""
import re

from . import housestyle

LABELS = ("Done", "Next", "Blocked", "Needs you")
LINE = re.compile(r"^\s*(?:[-*]\s*)?(Done|Next|Blocked|Needs you)\s*:\s*(.*)$", re.I)
MAX_LINE = 400
EMPTY = {"", "none", "n/a", "nothing", "no", "-"}


def parse(text):
    """Return {"Done": ..., "Next": ..., "Blocked": ..., "Needs you": ...} or None. Empty parts read as "none"."""
    found = {}
    current = None
    for raw in str(text or "").splitlines():
        match = LINE.match(raw)
        if match:
            current = next(label for label in LABELS if label.lower() == match.group(1).lower())
            if current not in found:
                found[current] = match.group(2).strip()
            else:
                current = None
        elif current and raw.strip() and raw[:1].isspace():
            found[current] += " " + raw.strip()          # a long part may run over more lines, and those lines are indented
        elif raw.strip():
            current = None                               # any other text ends the part
    if set(found) != set(LABELS):
        return None
    out = {}
    for label in LABELS:
        value = housestyle.fix(" ".join(found[label].split()))[:MAX_LINE].strip()
        out[label] = "none" if value.lower().rstrip(".") in EMPTY else value
    return out


def needs_owner(report):
    return report is not None and report["Needs you"] != "none"


def is_blocked(report):
    return report is not None and report["Blocked"] != "none"


def render(report, title=None):
    """The report as plain text for a chat message. Slack mrkdwn, so the labels are bold."""
    lines = ["*%s*" % title] if title else []
    lines += ["*%s:* %s" % (label, report[label]) for label in LABELS]
    return "\n".join(lines)
