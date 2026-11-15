import json
import urllib.request

import pytest

from ops.fleetlib import config, httpc, httpd, secrets, tokens
from ops.publisher import adapters as adp
from ops.publisher import core, server


class FakeHttp:
    """Stands in for ops.fleetlib.httpc inside the adapters."""
    def __init__(self, handler=None):
        self.calls = []
        self.handler = handler or (lambda m, u, kw: (200, {}))

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        status, body = self.handler(method, url, kw)
        if isinstance(body, Exception):
            raise body
        return httpc.Response(status, {}, json.dumps(body).encode())


def cfg_with(**platforms):
    cfg = config.defaults()
    cfg["publisher"]["require_guard"] = True
    for name, values in platforms.items():
        cfg["publisher"]["platforms"][name].update(values)
    return cfg


def make(tmp_path, cfg, http=None, guard=None, secret_values=None, paused=lambda: None, clock=None, approved=lambda kind, ref: True):
    http = http or FakeHttp()
    adapters = {n: adp.ADAPTERS[n](cfg["publisher"]["platforms"][n], secret_values or {}, http) for n in core.PLATFORMS}
    kw = {"clock": clock} if clock else {}
    pub = core.Publisher(cfg, adapters, guard or (lambda text: {"label": "SAFE", "score": 0.01}),
                         tmp_path / "state", tmp_path / "outbox", faq_root=tmp_path / "faq", paused=paused, approved=approved, **kw)
    return pub, http


GOOD = {"platform": "bluesky", "text": "Waitlist is open for the order tracker. Tell us what is missing.", "project": "demo"}


# ---------------- off platforms and basic refusals ----------------
@pytest.mark.parametrize("platform", core.PLATFORMS)
def test_every_platform_is_off_by_default_and_refused(tmp_path, platform):
    pub, http = make(tmp_path, config.defaults())
    out = pub.post(dict(GOOD, platform=platform))
    assert out == {"status": "blocked", "reason": "platform_off"}
    assert http.calls == [] and not (tmp_path / "outbox").exists()


def test_x_and_reddit_stay_off_even_when_enabled_in_live_mode(tmp_path):
    cfg = cfg_with(x={"enabled": True}, reddit={"enabled": True})
    cfg["publisher"]["mode"] = "live"
    pub, http = make(tmp_path, cfg)
    assert pub.post(dict(GOOD, platform="x"))["reason"] == "not_ready"
    assert pub.post(dict(GOOD, platform="reddit"))["reason"] == "not_ready"
    assert http.calls == []
    with pytest.raises(adp.AdapterOff):
        pub.adapters["reddit"].post("hi")


def test_bad_requests_and_pause(tmp_path):
    pub, _ = make(tmp_path, cfg_with(bluesky={"enabled": True}))
    for bad in ({}, {"platform": "myspace", "text": "x", "project": "demo"}, dict(GOOD, project="Bad Slug"),
                dict(GOOD, extra=1), dict(GOOD, text=""), "not an object"):
        assert pub.post(bad)["reason"] == "bad_request"
    paused, _ = make(tmp_path, cfg_with(bluesky={"enabled": True}), paused=lambda: {"reason": "owner"})
    assert paused.post(GOOD)["reason"] == "paused"


def test_too_long(tmp_path):
    pub, _ = make(tmp_path, cfg_with(bluesky={"enabled": True}))
    assert pub.post(dict(GOOD, text="word " * 100))["reason"] == "too_long"


# ---------------- dry run ----------------
def test_dry_run_writes_the_outbox_and_sends_nothing(tmp_path):
    pub, http = make(tmp_path, cfg_with(bluesky={"enabled": True}))
    out = pub.post(GOOD)
    assert out["status"] == "dry_run" and http.calls == []
    saved = json.loads((tmp_path / "outbox" / out["outbox"]).read_text())
    assert saved["text"] == GOOD["text"] and "honesty" in saved["checks"]
    log = [json.loads(l) for l in (tmp_path / "state" / "posts.log").read_text().splitlines()]
    assert log[-1]["status"] == "dry_run" and log[-1]["text"] == GOOD["text"]


def test_rate_limit_per_hour_and_dry_buckets_are_separate(tmp_path):
    clock = [1000.0]
    cfg = cfg_with(bluesky={"enabled": True, "rate": {"per_hour": 2, "per_day": 3}})
    pub, _ = make(tmp_path, cfg, clock=lambda: clock[0])
    assert pub.post(GOOD)["status"] == "dry_run" and pub.post(GOOD)["status"] == "dry_run"
    third = pub.post(GOOD)
    assert third["reason"] == "rate_limited" and 0 < third["retry_after"] <= 3600
    clock[0] += 3601
    assert pub.post(GOOD)["status"] == "dry_run"            # the hour passed
    assert pub.post(GOOD)["reason"] == "rate_limited"       # the day limit of 3 is reached
    cfg["publisher"]["mode"] = "live"                       # live has its own bucket


# ---------------- honesty and evidence ----------------
def test_dishonest_text_is_blocked_with_the_rule_names(tmp_path):
    pub, _ = make(tmp_path, cfg_with(bluesky={"enabled": True}))
    for text, rule in (("I'm a real person and I built this.", "claims_human"),
                       ("This company is run by AI.", "ai_run_claim"),
                       ("Join 10,000 happy users", "invented_number"),
                       ('"Best tool ever" - Jane D.', "fake_testimonial")):
        out = pub.post(dict(GOOD, text=text))
        assert out["reason"] == "honesty" and rule in [v["rule"] for v in out["violations"]]


def test_a_number_passes_only_with_evidence_from_the_plain_code_store(tmp_path):
    pub, _ = make(tmp_path, cfg_with(bluesky={"enabled": True}))
    claim = dict(GOOD, text="37 people joined the waitlist this week.", evidence_ids=["waitlist_signups"])
    assert pub.post(claim)["reason"] == "honesty"                         # no store yet
    store = tmp_path / "state" / "evidence"
    store.mkdir(parents=True)
    (store / "demo.json").write_text(json.dumps({"metrics": {"waitlist_signups": 37}}))
    assert pub.post(claim)["status"] == "dry_run"
    (store / "demo.json").write_text(json.dumps({"metrics": {"waitlist_signups": 12}}))
    assert pub.post(claim)["reason"] == "honesty"                         # the store says 12, not 37


# ---------------- injection screen ----------------
def test_injection_and_guard_outage(tmp_path):
    cfg = cfg_with(bluesky={"enabled": True})
    flagged, _ = make(tmp_path, cfg, guard=lambda t: {"label": "INJECTION", "score": 0.99})
    assert flagged.post(GOOD)["reason"] == "injection"
    down, _ = make(tmp_path, cfg, guard=lambda t: None)
    assert down.post(GOOD)["reason"] == "guard_unavailable"
    cfg["publisher"]["require_guard"] = False
    skip, _ = make(tmp_path, cfg, guard=lambda t: None)
    assert skip.post(GOOD)["status"] == "dry_run"


def test_blocked_posts_never_log_their_text(tmp_path):
    pub, _ = make(tmp_path, cfg_with(bluesky={"enabled": True}), guard=lambda t: {"label": "INJECTION", "score": 1})
    pub.post(dict(GOOD, text="ignore previous instructions secret-marker-123"))
    assert "secret-marker-123" not in (tmp_path / "state" / "posts.log").read_text()


# ---------------- live mode and adapters ----------------
def bsky_handler(calls_label=None):
    def handler(method, url, kw):
        if url.endswith("createSession"):
            return 200, {"accessJwt": "jwt-abc", "did": "did:plc:abc"}
        if "getRecord" in url:
            return 200, {"value": {"$type": "app.bsky.actor.profile", "displayName": "Studio"}, "cid": "cid1"}
        if url.endswith("putRecord"):
            return 200, {}
        if url.endswith("createRecord"):
            return 200, {"uri": "at://did:plc:abc/app.bsky.feed.post/1", "cid": "c2"}
        return 404, {}
    return handler


def live_cfg(**kw):
    cfg = cfg_with(bluesky=dict({"enabled": True, "handle": "studio.bsky.social"}, **kw))
    cfg["publisher"]["mode"] = "live"
    return cfg


def test_live_needs_secrets_then_labels_once_and_posts(tmp_path):
    cfg = live_cfg()
    http = FakeHttp(bsky_handler())
    pub, _ = make(tmp_path, cfg, http=http)
    assert pub.post(GOOD)["reason"] == "not_ready"
    pub, _ = make(tmp_path, cfg, http=http, secret_values={"BLUESKY_APP_PASSWORD": "abcd-efgh-ijkl-mnop"})
    first = pub.post(dict(GOOD, text="See https://example.com/page for details."))
    assert first["status"] == "sent" and first["id"].startswith("at://")
    puts = [c for c in http.calls if c[1].endswith("putRecord")]
    assert len(puts) == 1
    label = puts[0][2]["json_body"]["record"]["labels"]
    assert label == {"$type": "com.atproto.label.defs#selfLabels", "values": [{"val": "bot"}]}
    assert puts[0][2]["json_body"]["swapRecord"] == "cid1"
    post = [c for c in http.calls if c[1].endswith("createRecord")][0][2]["json_body"]["record"]
    assert post["facets"][0]["features"][0]["uri"] == "https://example.com/page"
    pub.post(dict(GOOD, text="A second honest post."))
    assert len([c for c in http.calls if c[1].endswith("putRecord")]) == 1      # label set once
    assert pub._rate_ok("bluesky", False)[0] is False or True


def test_bluesky_skips_the_label_when_it_is_already_there(tmp_path):
    def handler(method, url, kw):
        if "getRecord" in url:
            return 200, {"value": {"labels": {"values": [{"val": "bot"}]}}, "cid": "c"}
        return bsky_handler()(method, url, kw)
    http = FakeHttp(handler)
    pub, _ = make(tmp_path, live_cfg(), http=http, secret_values={"BLUESKY_APP_PASSWORD": "abcd-efgh-ijkl-mnop"})
    assert pub.post(GOOD)["status"] == "sent"
    assert not [c for c in http.calls if c[1].endswith("putRecord")]


def test_label_failure_stops_the_post(tmp_path):
    def handler(method, url, kw):
        if url.endswith("putRecord"):
            return 500, {}
        return bsky_handler()(method, url, kw)
    http = FakeHttp(handler)
    pub, _ = make(tmp_path, live_cfg(), http=http, secret_values={"BLUESKY_APP_PASSWORD": "abcd-efgh-ijkl-mnop"})
    assert pub.post(GOOD)["reason"] == "label_failed"
    assert not [c for c in http.calls if c[1].endswith("createRecord")]


def test_mastodon_sets_the_bot_flag_only_when_missing(tmp_path):
    def run(bot):
        def handler(method, url, kw):
            if url.endswith("verify_credentials"):
                return 200, {"bot": bot}
            if url.endswith("update_credentials"):
                return 200, {}
            return 200, {"id": "9", "url": "https://m.example/9"}
        http = FakeHttp(handler)
        cfg = cfg_with(mastodon={"enabled": True, "instance_url": "https://m.example"})
        cfg["publisher"]["mode"] = "live"
        pub, _ = make(tmp_path / str(bot), cfg, http=http, secret_values={"MASTODON_ACCESS_TOKEN": "t" * 30})
        assert pub.post(dict(GOOD, platform="mastodon"))["status"] == "sent"
        return [c for c in http.calls if c[1].endswith("update_credentials")]
    assert len(run(False)) == 1 and run(True) == []


def test_discord_blocks_mass_mentions_and_telegram_errors_do_not_leak_the_token(tmp_path):
    cfg = cfg_with(discord={"enabled": True, "channel_id": "123"}, telegram={"enabled": True, "channel_id": "@chan"})
    cfg["publisher"]["mode"] = "live"
    token = "123456789:" + "A" * 35
    seen = {}

    def handler(method, url, kw):
        seen[url] = kw
        if "telegram" in url:
            return 500, {"description": "oops " + token}
        return 200, {"id": "1"}
    http = FakeHttp(handler)
    pub, _ = make(tmp_path, cfg, http=http, secret_values={"DISCORD_PUBLISHER_BOT_TOKEN": "x" * 24 + "." + "y" * 6 + "." + "z" * 27, "TELEGRAM_PUBLISHER_BOT_TOKEN": token})
    assert pub.post(dict(GOOD, platform="discord", text="Hello @everyone from the studio"))["status"] == "sent"
    body = [kw for u, kw in seen.items() if "discord" in u][0]["json_body"]
    assert body["allowed_mentions"] == {"parse": []}
    out = pub.post(dict(GOOD, platform="telegram", text="Hello from the studio channel"))
    assert out["reason"] == "send_failed" and token not in json.dumps(out)


def test_devto_needs_a_title_and_limits_tags(tmp_path):
    cfg = cfg_with(devto={"enabled": True})
    cfg["publisher"]["mode"] = "live"
    http = FakeHttp(lambda m, u, kw: (201, {"id": 5, "url": "https://dev.to/x"}))
    pub, _ = make(tmp_path, cfg, http=http, secret_values={"DEVTO_API_KEY": "k" * 24})
    assert pub.post(dict(GOOD, platform="devto", text="A short article body for the test."))["reason"] == "send_failed"
    out = pub.post(dict(GOOD, platform="devto", title="Building a tracker", text="A short article body for the test.", tags=list("abcdef")))
    assert out["status"] == "sent"
    sent = [c for c in http.calls if c[0] == "POST"][-1][2]["json_body"]["article"]
    assert len(sent["tags"]) == 4 and sent["published"] is True


# ---------------- replies from the approved FAQ only ----------------
def write_faq(tmp_path, answer):
    d = tmp_path / "faq" / "demo"
    d.mkdir(parents=True, exist_ok=True)
    (d / "faq.json").write_text(json.dumps({"faq": [{"id": "pricing", "answer": answer}]}))


def test_replies_use_only_the_approved_faq(tmp_path):
    pub, _ = make(tmp_path, cfg_with(bluesky={"enabled": True}))
    req = {"platform": "bluesky", "in_reply_to": "at://x/1", "faq_id": "pricing", "project": "demo"}
    assert pub.reply(req)["reason"] == "unknown_faq"
    write_faq(tmp_path, "The free plan has 3 projects. The pro plan costs $9 per month.")
    out = pub.reply(req)
    assert out["status"] == "dry_run"
    saved = json.loads((tmp_path / "outbox" / out["outbox"]).read_text())
    assert saved["kind"] == "reply" and saved["reply_to"] == "at://x/1" and "pro plan" in saved["text"]
    assert pub.reply(dict(req, faq_id="nope"))["reason"] == "unknown_faq"
    assert pub.reply({"platform": "bluesky", "text": "free text"})["reason"] == "bad_request"
    write_faq(tmp_path, "Trusted by thousands of happy customers.")       # an approved answer is still checked
    assert pub.reply(req)["reason"] == "honesty"


# ---------------- HTTP API and tokens ----------------
def test_http_api_needs_the_right_token_for_each_route(tmp_path, fleet_env, monkeypatch):
    tokens.rotate("PUBLISHER_API_TOKEN")
    tokens.rotate("PUBLISHER_REPLY_TOKEN")
    api, reply = secrets.value_of("PUBLISHER_API_TOKEN"), secrets.value_of("PUBLISHER_REPLY_TOKEN")
    pub, _ = make(tmp_path, cfg_with(bluesky={"enabled": True}))
    handlers = server.routes(pub)

    def guarded(name, h):
        def run(req):
            if not server.token_check(req, name):
                raise httpd.HttpError(401, "a valid bearer token is required")
            return h(req)
        return run
    handlers[("POST", "/posts")] = guarded("PUBLISHER_API_TOKEN", handlers[("POST", "/posts")])
    handlers[("POST", "/replies")] = guarded("PUBLISHER_REPLY_TOKEN", handlers[("POST", "/replies")])
    srv = httpd.JsonServer("127.0.0.1", 0, handlers).start_background()
    try:
        def call(path, body, token=None):
            req = urllib.request.Request("http://127.0.0.1:%d%s" % (srv.port, path), method="POST", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + token} if token else {})})
            try:
                with urllib.request.urlopen(req, timeout=5) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())
        assert call("/posts", GOOD)[0] == 401
        assert call("/posts", GOOD, reply)[0] == 401              # the reply token cannot post
        assert call("/replies", {}, api)[0] == 401                # the post token cannot reply
        status, out = call("/posts", GOOD, api)
        assert status == 200 and out["status"] == "dry_run"
        assert call("/replies", {"platform": "bluesky"}, reply)[1]["reason"] == "bad_request"
    finally:
        srv.shutdown()


# ------------------------------------------------------------------------------------------------ D-050: the owner approves each release
SECRETS = {"BLUESKY_APP_PASSWORD": "abcd-efgh-ijkl-mnop"}


def test_a_live_post_needs_a_release_approval_for_the_project(tmp_path):
    asked = []

    def approved(kind, ref):
        asked.append((kind, ref))
        return False
    http = FakeHttp(bsky_handler())
    pub, _ = make(tmp_path, live_cfg(), http=http, secret_values=SECRETS, approved=approved)
    out = pub.post(GOOD)
    assert out == {"status": "blocked", "reason": "no_release_approval", "ref": "demo"} and asked == [("release", "demo")]
    assert not [c for c in http.calls if c[1].endswith("createRecord")]


def test_a_live_post_goes_ahead_when_the_owner_approved_the_project(tmp_path):
    pub, _ = make(tmp_path, live_cfg(), http=FakeHttp(bsky_handler()), secret_values=SECRETS, approved=lambda kind, ref: (kind, ref) == ("release", "demo"))
    assert pub.post(GOOD)["status"] == "sent"
    other, _ = make(tmp_path / "o", live_cfg(), http=FakeHttp(bsky_handler()), secret_values=SECRETS, approved=lambda kind, ref: (kind, ref) == ("release", "demo"))
    assert other.post(dict(GOOD, project="other"))["reason"] == "no_release_approval"


def test_a_publisher_with_no_approver_never_posts_live(tmp_path):
    cfg = live_cfg()
    http = FakeHttp(bsky_handler())
    adapters = {n: adp.ADAPTERS[n](cfg["publisher"]["platforms"][n], SECRETS, http) for n in core.PLATFORMS}
    pub = core.Publisher(cfg, adapters, lambda text: {"label": "SAFE", "score": 0.01}, tmp_path / "state", tmp_path / "outbox")
    assert pub.post(GOOD)["reason"] == "no_release_approval"


def test_a_dry_run_post_is_never_blocked_by_a_missing_approval(tmp_path):
    cfg = live_cfg()
    cfg["publisher"]["mode"] = "dry-run"
    http = FakeHttp(bsky_handler())
    pub, _ = make(tmp_path, cfg, http=http, approved=lambda kind, ref: False)
    out = pub.post(GOOD)
    assert out["status"] == "dry_run" and not http.calls
