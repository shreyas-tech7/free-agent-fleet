"""The intake: outside messages become inbox items. Plain code. The feedback agent reads only what this writes.

For each message:
  1  checks: a known source, a valid project name, text of a sane size
  2  injection screen. If the screen is down the message is refused (fail closed). A flagged message goes to the
     quarantine folder, which no agent can see
  3  minimize: the user becomes a hash. Emails, phone numbers, card numbers, IP addresses, handles, link tokens,
     and keys are replaced by short tags
  4  limits: a cap for each source and each hour, and a cap for the size of the inbox
  5  one JSON file in the inbox, with the schema that the feedback agent expects

The person who wrote the message never reaches an agent. Only the hash and the cleaned text do.
"""
import hashlib
import hmac
import json
import os
import pathlib
import re
import secrets as pysecrets
import time
import urllib.parse

from ops.fleetlib import jsonio, log, redact
from ops.guard import rules

LOG = log.get("intake")
SOURCES = ("bluesky", "mastodon", "discord", "telegram", "devto", "github", "form", "email", "x", "reddit")
SLUG = re.compile(r"^[a-z0-9][a-z0-9\-]{0,40}$")
MAX_INPUT = 8000
MAX_TEXT = 2000
PER_SOURCE_PER_HOUR = 200
MAX_INBOX = 3000

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<![\w/])\+?\d[\d\s().\-]{7,}\d(?![\w/])")
_CARD = re.compile(r"(?<!\d)(?:\d[ \-]?){12,18}\d(?!\d)")
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_URL = re.compile(r"https?://[^\s<>\"')\]]+")
_HANDLE = re.compile(r"(?<![\w@])@[A-Za-z0-9_][A-Za-z0-9_.\-]{1,60}")
_DID = re.compile(r"\bdid:(?:plc|web):[A-Za-z0-9._:%\-]+")


class IntakeError(Exception):
    pass


def minimize(text):
    """Replace personal and secret details with short tags. The meaning of the message stays."""
    text = str(text)

    def url(m):
        parsed = urllib.parse.urlsplit(m.group(0))
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    text = _URL.sub(url, text)                       # first, so a token in a link goes away with the query
    text = redact.redact_text(text)
    text = _EMAIL.sub("[email]", text)
    text = _DID.sub("[id]", text)
    text = _HANDLE.sub("[handle]", text)
    text = _CARD.sub("[number]", text)
    text = _IPV4.sub("[ip]", text)
    text = _PHONE.sub("[phone]", text)
    text = "".join(ch if (ch >= " " or ch in "\n\t") else " " for ch in text)
    return re.sub(r"[ \t]+", " ", text).strip()


class Intake:
    def __init__(self, inbox, quarantine, guard, salt_file, clock=time.time, per_source_per_hour=PER_SOURCE_PER_HOUR, max_inbox=MAX_INBOX):
        self.inbox = pathlib.Path(inbox)
        self.quarantine = pathlib.Path(quarantine)
        self.guard = guard                    # callable(text) -> {"label": ..., "score": ...}. It raises when the screen is down.
        self.salt_file = pathlib.Path(salt_file)
        self.clock = clock
        self.per_source = per_source_per_hour
        self.max_inbox = max_inbox
        self._stamps = {}

    # ---------------------------------------------------------------- helpers
    def salt(self):
        try:
            return self.salt_file.read_bytes()
        except OSError:
            self.salt_file.parent.mkdir(parents=True, exist_ok=True)
            data = pysecrets.token_bytes(32)
            fd = os.open(str(self.salt_file), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            return data

    def user_hash(self, source, user_id):
        digest = hmac.new(self.salt(), ("%s:%s" % (source, user_id)).encode(), hashlib.sha256).hexdigest()
        return "u_" + digest[:12]

    def _rate_ok(self, source):
        now = self.clock()
        stamps = [t for t in self._stamps.get(source, []) if now - t < 3600]
        self._stamps[source] = stamps
        return len(stamps) < self.per_source

    def _inbox_size(self):
        try:
            return sum(1 for _ in self.inbox.glob("f-*.json"))
        except OSError:
            return 0

    def _write(self, folder, name, record, mode):
        folder.mkdir(parents=True, exist_ok=True)
        jsonio.write_json(folder / name, record, mode=mode)

    # ---------------------------------------------------------------- the pipeline
    def accept(self, source, text, user_id, project, in_reply_to=None):
        """Returns {"status": "stored"|"quarantined"|"refused", "reason": text, "id": name}. It never raises for bad input."""
        if source not in SOURCES:
            return {"status": "refused", "reason": "unknown_source"}
        if not isinstance(project, str) or not SLUG.match(project):
            return {"status": "refused", "reason": "bad_project"}
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_INPUT:
            return {"status": "refused", "reason": "bad_text"}
        if not self._rate_ok(source):
            return {"status": "refused", "reason": "rate_limited"}
        if self._inbox_size() >= self.max_inbox:
            return {"status": "refused", "reason": "inbox_full"}
        # The screen sees the original text, so hidden characters and encodings show up.
        try:
            verdict = self.guard(text)
        except Exception:
            verdict = None
        if not isinstance(verdict, dict) or "label" not in verdict:         # the guard client returns None when the screen is down
            LOG.warn("guard_down", source=source)
            return {"status": "refused", "reason": "guard_unavailable"}
        digest = hashlib.sha256(("%s|%s|%s" % (source, user_id, text)).encode()).hexdigest()
        stamp = int(self.clock())
        user = self.user_hash(source, user_id)
        if verdict.get("label") != "SAFE" or "hidden_unicode" in rules.scan(text)[1]:
            name = "q-%d-%s.json" % (stamp, digest[:8])
            self._write(self.quarantine, name, {"source": source, "project": project, "user_hash": user, "score": verdict.get("score"),
                                                "reasons": verdict.get("reasons", []), "snippet": minimize(text)[:300], "received": stamp}, 0o600)
            self._stamps.setdefault(source, []).append(self.clock())
            LOG.warn("quarantined", source=source, score=verdict.get("score"))
            return {"status": "quarantined", "reason": "injection", "id": name}
        if any(self.inbox.glob("f-*-%s.json" % digest[:8])):
            return {"status": "refused", "reason": "duplicate"}
        name = "f-%d-%s.json" % (stamp, digest[:8])
        record = {"id": name[:-5], "source": source, "project": project, "received": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stamp)),
                  "user_hash": user, "text": minimize(text)[:MAX_TEXT], "in_reply_to": (str(in_reply_to)[:300] if in_reply_to else None)}
        self._write(self.inbox, name, record, 0o640)
        self._stamps.setdefault(source, []).append(self.clock())
        return {"status": "stored", "reason": "ok", "id": name}
