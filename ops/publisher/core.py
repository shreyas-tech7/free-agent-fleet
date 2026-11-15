"""The Publisher pipeline. It is the only part of the fleet that posts in public.

Order of checks for every post and every reply:
  1 paused         the kill switch flag exists
  2 bad_request    the JSON does not match the schema
  3 unknown_platform / platform_off / not_ready
  4 too_long       longer than the platform allows
  5 honesty        the honesty filter (core/policy/honesty.py). Numbers and quotes need evidence.
  6 injection      the injection screen. If it is down, the post is refused (fail closed).
  7 rate_limited   per platform, per hour and per day
  8 label_failed   the automated-account label could not be set (live mode only)
Then: dry-run mode writes the post to the outbox. Live mode sends it through the adapter.
Evidence comes only from plain-code stores. An agent names an evidence id. It never supplies a value.
"""
import hashlib
import json
import pathlib
import re
import threading
import time

from core.policy import honesty
from ops.fleetlib import jsonio, log, schema
from ops.publisher import adapters as adp

LOG = log.get("publisher")
PLATFORMS = ["bluesky", "mastodon", "discord", "telegram", "devto", "github", "x", "reddit"]
SLUG = r"^[a-z0-9][a-z0-9\-]{0,40}$"

POST_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["platform", "text", "project"],
    "properties": {
        "platform": {"enum": PLATFORMS},
        "text": {"type": "string", "minLength": 1, "maxLength": 100000},
        "project": {"type": "string", "pattern": SLUG},
        "title": {"type": "string", "maxLength": 200},
        "tags": {"type": "array", "maxItems": 10, "items": {"type": "string", "maxLength": 40}},
        "media": {"type": "array", "maxItems": 4},
        "evidence_ids": {"type": "array", "maxItems": 20, "items": {"type": "string", "maxLength": 80}},
    },
}
REPLY_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["platform", "in_reply_to", "faq_id", "project"],
    "properties": {
        "platform": {"enum": PLATFORMS},
        "in_reply_to": {"type": "string", "minLength": 1, "maxLength": 300},
        "faq_id": {"type": "string", "pattern": r"^[a-z0-9_\-]{1,60}$"},
        "project": {"type": "string", "pattern": SLUG},
    },
}


class Publisher:
    def __init__(self, cfg, adapters, guard, state_dir, outbox_dir, faq_root=None, paused=lambda: None, clock=time.time, approved=None):
        self.approved = approved          # callable(kind, ref) -> bool. True when the owner approved. None means never. See D-050.
        self.cfg = cfg
        self.adapters = adapters
        self.guard = guard
        self.state = pathlib.Path(state_dir)
        self.outbox = pathlib.Path(outbox_dir)
        self.faq_root = pathlib.Path(faq_root) if faq_root else None
        self.paused = paused
        self.clock = clock
        self.lock = threading.Lock()
        self._labeled = set()

    # ---- helpers ----
    @property
    def mode(self):
        return (self.cfg.get("publisher") or {}).get("mode", "dry-run")

    def platform_cfg(self, name):
        return ((self.cfg.get("publisher") or {}).get("platforms") or {}).get(name) or {}

    def _evidence(self, project, ids):
        store = jsonio.read_json(self.state / "evidence" / ("%s.json" % project), {}) or {}
        out = []
        for eid in ids or []:
            if eid in (store.get("metrics") or {}):
                out.append({"kind": "metric", "id": eid, "value": store["metrics"][eid]})
            for t in store.get("testimonials") or []:
                if t.get("id") == eid:
                    out.append({"kind": "testimonial", "id": eid, "text": t.get("text", "")})
        return out

    def _rate_ok(self, platform, dry):
        limits = (self.platform_cfg(platform).get("rate") or {})
        per_hour, per_day = int(limits.get("per_hour", 1)), int(limits.get("per_day", 4))
        path = self.state / "ratelimit.json"
        data = jsonio.read_json(path, {}) or {}
        key = platform + (":dry" if dry else "")
        now = self.clock()
        stamps = [t for t in data.get(key, []) if now - t < 86400]
        in_hour = [t for t in stamps if now - t < 3600]
        if len(in_hour) >= per_hour:
            return False, int(3600 - (now - min(in_hour)))
        if len(stamps) >= per_day:
            return False, int(86400 - (now - min(stamps)))
        return True, 0

    def _rate_record(self, platform, dry):
        path = self.state / "ratelimit.json"
        data = jsonio.read_json(path, {}) or {}
        key = platform + (":dry" if dry else "")
        now = self.clock()
        data[key] = [t for t in data.get(key, []) if now - t < 86400] + [now]
        jsonio.write_json(path, data, mode=0o640)

    def _log(self, kind, req, text, status, reason="", ref=""):
        record = {"ts": int(self.clock()), "kind": kind, "platform": req.get("platform"), "project": req.get("project"),
                  "status": status, "reason": reason, "chars": len(text or ""),
                  "text_sha256": hashlib.sha256((text or "").encode()).hexdigest(), "ref": ref}
        if status in ("sent", "dry_run"):
            record["text"] = text
        try:
            self.state.mkdir(parents=True, exist_ok=True)
            with open(self.state / "posts.log", "a") as fh:
                fh.write(json.dumps(record, sort_keys=True) + "\n")
        except OSError:
            pass
        LOG.info("post", kind=kind, platform=req.get("platform"), status=status, reason=reason)

    def _blocked(self, kind, req, text, reason, **extra):
        self._log(kind, req if isinstance(req, dict) else {}, text, "blocked", reason)
        return dict({"status": "blocked", "reason": reason}, **extra)

    # ---- the pipeline ----
    def _publish(self, kind, req, text, title=None, tags=None, reply_to=None, media=None):
        platform = req["platform"]
        if self.paused():
            return self._blocked(kind, req, text, "paused")
        pcfg = self.platform_cfg(platform)
        if not pcfg.get("enabled"):
            return self._blocked(kind, req, text, "platform_off")
        adapter = self.adapters.get(platform)
        dry = self.mode != "live"
        if adapter is None:
            return self._blocked(kind, req, text, "platform_off")
        if not dry:
            ok, why = adapter.ready()
            if not ok:
                return self._blocked(kind, req, text, "not_ready", detail=why)
            if not (self.approved and self.approved("release", req["project"])):
                return self._blocked(kind, req, text, "no_release_approval", ref=req["project"])      # the owner approves each release. See D-050.
        limit = int(pcfg.get("max_chars") or adapter.max_chars)
        if len(text) > limit:
            return self._blocked(kind, req, text, "too_long", limit=limit)
        evidence = self._evidence(req["project"], req.get("evidence_ids"))
        verdict = honesty.check((title + "\n" if title else "") + text, honesty.MARKETING, evidence)
        if not verdict.allowed:
            return self._blocked(kind, req, text, "honesty",
                                 violations=[{"rule": v.rule, "why": v.why} for v in verdict.violations])
        if (self.cfg.get("publisher") or {}).get("require_guard", True):
            scan = self.guard((title + "\n" if title else "") + text)
            if scan is None:
                return self._blocked(kind, req, text, "guard_unavailable")
            if scan.get("label") != "SAFE":
                return self._blocked(kind, req, text, "injection", score=scan.get("score"))
        ok, retry = self._rate_ok(platform, dry)
        if not ok:
            return self._blocked(kind, req, text, "rate_limited", retry_after=retry)
        if dry:
            self.outbox.mkdir(parents=True, exist_ok=True)
            name = "%d-%s-%s.json" % (int(self.clock()), platform, hashlib.sha256(text.encode()).hexdigest()[:8])
            jsonio.write_json(self.outbox / name, {"platform": platform, "project": req["project"], "kind": kind, "text": text,
                                                   "title": title, "tags": tags, "reply_to": reply_to, "media": media,
                                                   "checks": ["paused", "schema", "platform", "length", "honesty", "injection", "rate"]},
                              mode=0o640)
            self._rate_record(platform, True)
            self._log(kind, req, text, "dry_run", ref=name)
            return {"status": "dry_run", "outbox": name}
        with self.lock:
            if platform not in self._labeled:
                try:
                    adapter.ensure_label()
                except adp.AdapterError as exc:
                    return self._blocked(kind, req, text, "label_failed", detail=str(exc))
                self._labeled.add(platform)
            try:
                result = adapter.post(text, title=title, tags=tags, reply_to=reply_to, media=media)
            except adp.AdapterError as exc:
                return self._blocked(kind, req, text, "send_failed", detail=str(exc))
            self._rate_record(platform, False)
        self._log(kind, req, text, "sent", ref=str(result.get("id", "")))
        return dict({"status": "sent"}, **result)

    def post(self, req):
        problems = schema.validate(req, POST_SCHEMA) if isinstance(req, dict) else ["the request must be an object"]
        if problems:
            return self._blocked("post", req if isinstance(req, dict) else {}, "", "bad_request", problems=problems[:5])
        return self._publish("post", req, req["text"], req.get("title"), req.get("tags"), None, req.get("media"))

    def reply(self, req):
        problems = schema.validate(req, REPLY_SCHEMA) if isinstance(req, dict) else ["the request must be an object"]
        if problems:
            return self._blocked("reply", req if isinstance(req, dict) else {}, "", "bad_request", problems=problems[:5])
        faq = jsonio.read_json((self.faq_root or pathlib.Path("/nonexistent")) / req["project"] / "faq.json", {}) or {}
        answer = next((i.get("answer") for i in faq.get("faq", []) if i.get("id") == req["faq_id"]), None)
        if not answer:
            return self._blocked("reply", req, "", "unknown_faq")
        return self._publish("reply", req, answer, None, None, req["in_reply_to"], None)

    def platforms(self):
        out = {}
        for name in PLATFORMS:
            pcfg = self.platform_cfg(name)
            adapter = self.adapters.get(name)
            ready = adapter.ready() if adapter else (False, "no adapter")
            out[name] = {"enabled": bool(pcfg.get("enabled")), "ready": bool(ready[0]), "detail": ready[1]}
        return {"mode": self.mode, "platforms": out}
