"""One free test call for each key. `fleetctl secret check` uses this module.

Each function returns (state, detail). The state is one of:
  ok            the provider accepted the key
  failed        the provider refused the key, or the call went wrong
  skipped       this key has no test call, or the test needs a setting that is not there yet
  not set       the key has no value
  unreachable   the network call did not finish

The value of a key never appears in a result, a message, or an exception.
"""
import smtplib

from ops.fleetlib import config, httpc, secrets


def _http(http, method, url, headers=None, json_body=None, timeout=20):
    try:
        resp = http.request(method, url, headers=headers or {}, json_body=json_body, timeout=timeout)
    except httpc.TransportError:
        return None
    return resp


def _status_result(resp):
    if resp is None:
        return "unreachable", "no answer from the provider"
    if 200 <= resp.status < 300:
        return "ok", "status %d" % resp.status
    if resp.status in (401, 403):
        return "failed", "the provider refused the key (status %d)" % resp.status
    return "failed", "unexpected status %d" % resp.status


def _generic(spec, value, http):
    headers = {}
    if spec.get("auth") == "bearer":
        headers["Authorization"] = "Bearer " + value
    elif spec.get("auth") == "header":
        headers[spec["header"]] = spec.get("prefix", "") + value
    return _status_result(_http(http, spec.get("method", "GET"), spec["url"], headers))


def _openai_probe(spec, value, http):
    """One chat call with max_tokens 1. These providers list their models without a key, so a model list proves nothing.
    A 402 means that the key works but the account has no free credit. It never charges, because no balance exists."""
    body = {"model": spec["model"], "max_tokens": 1, "messages": [{"role": "user", "content": "Reply with OK."}]}
    try:
        resp = http.request("POST", spec["url"], headers={"Authorization": "Bearer " + value}, json_body=body, timeout=30)
    except httpc.TransportError:
        return "unreachable", "no answer from the provider"
    if resp.status == 402:
        return "failed", "the key is accepted, but the account has no free credit (status 402). Do not add money. Wait for the free grant"
    return _status_result(resp)


def _router_models(cfg, value, http):
    url = "http://127.0.0.1:%d/v1/models" % config.port(cfg, "router")
    return _status_result(_http(http, "GET", url, {"Authorization": "Bearer " + value}, timeout=10))


def _telegram(value, http):
    resp = _http(http, "GET", "https://api.telegram.org/bot%s/getMe" % value)
    state, detail = _status_result(resp)
    if state == "ok":
        try:
            if resp.json().get("ok") is not True:
                return "failed", "Telegram did not accept the token"
        except ValueError:
            return "failed", "Telegram gave an answer that is not JSON"
    return state, detail


def _slack(url, value, http):
    """Slack answers 200 with {"ok": false} for a bad token. So this reads the body."""
    state, detail = "unreachable", "no answer from Slack"
    resp = _http(http, "POST", url, {"Authorization": "Bearer " + value})
    state, detail = _status_result(resp)
    if state == "ok":
        try:
            data = resp.json()
        except ValueError:
            return "failed", "Slack gave an answer that is not JSON"
        if data.get("ok") is not True:
            return "failed", "Slack refused the token (%s)" % str(data.get("error", "no reason"))[:40]
    return state, detail


def _bluesky(cfg, value, http):
    handle = config.get(cfg, "publisher.platforms.bluesky.handle", "")
    if not handle:
        return "skipped", "set publisher.platforms.bluesky.handle first"
    pds = (config.get(cfg, "publisher.platforms.bluesky.pds_url", "") or "https://bsky.social").rstrip("/")
    resp = _http(http, "POST", pds + "/xrpc/com.atproto.server.createSession", json_body={"identifier": handle, "password": value})
    state, detail = _status_result(resp)
    if state == "ok":
        try:
            if not resp.json().get("accessJwt"):
                return "failed", "Bluesky gave no session"
        except ValueError:
            return "failed", "Bluesky gave an answer that is not JSON"
    return state, detail


def _mastodon(cfg, value, http):
    base = (config.get(cfg, "publisher.platforms.mastodon.instance_url", "") or "").rstrip("/")
    if not base.startswith("https://"):
        return "skipped", "set publisher.platforms.mastodon.instance_url first (an https address)"
    return _status_result(_http(http, "GET", base + "/api/v1/accounts/verify_credentials", {"Authorization": "Bearer " + value}))


def _smtp(cfg, value, smtp_module):
    e = config.get(cfg, "briefing.email", {}) or {}
    if not e.get("smtp_host"):
        return "skipped", "set briefing.email.smtp_host first"
    user = e.get("smtp_user") or e.get("from_address")
    if not user:
        return "skipped", "set briefing.email.smtp_user first"
    port = int(e.get("smtp_port", 587))
    try:
        if port == 465:
            client = smtp_module.SMTP_SSL(e["smtp_host"], port, timeout=20)
        else:
            client = smtp_module.SMTP(e["smtp_host"], port, timeout=20)
        try:
            if port != 465:
                client.starttls()
            client.login(user, value)
        finally:
            try:
                client.quit()
            except (smtplib.SMTPException, OSError):
                pass
    except smtplib.SMTPAuthenticationError:
        return "failed", "the mail server refused the login"
    except (smtplib.SMTPException, OSError):
        return "unreachable", "could not talk to the mail server"
    return "ok", "login accepted"


def check_secret(name, cfg=None, http=httpc, smtp_module=smtplib):
    """Run the one test call that config/secrets.yaml lists for this key."""
    entry = secrets.get_secret(name)
    state = secrets.is_set(name)
    if state is None:
        return "failed", "cannot read the secret file. Run with sudo."
    if not state:
        return "not set", ""
    cfg = cfg or config.load()
    spec = entry.get("check") or {}
    kind = spec.get("type")
    if kind == "skip":
        return "skipped", spec.get("reason", "no test call")
    value = secrets.value_of(name)
    if kind == "openai_probe":
        return _openai_probe(spec, value, http)
    if kind == "router_models":
        return _router_models(cfg, value, http)
    if kind == "telegram_getme":
        return _telegram(value, http)
    if kind == "slack_auth_test":
        return _slack("https://slack.com/api/auth.test", value, http)
    if kind == "slack_app_token":
        return _slack("https://slack.com/api/apps.connections.open", value, http)
    if kind == "bluesky_session":
        return _bluesky(cfg, value, http)
    if kind == "mastodon_verify":
        return _mastodon(cfg, value, http)
    if kind == "smtp_login":
        return _smtp(cfg, value, smtp_module)
    if spec.get("url"):
        return _generic(spec, value, http)
    return "skipped", "no test call is defined"
