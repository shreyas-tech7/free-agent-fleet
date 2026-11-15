"""The Publisher service on 127.0.0.1:8098.

  POST /posts    {platform, text, project, title?, tags?, media?, evidence_ids?}   token: PUBLISHER_API_TOKEN
  POST /replies  {platform, in_reply_to, faq_id, project}                          token: PUBLISHER_REPLY_TOKEN
  GET  /platforms, GET /health

The service reads the social tokens from its own secret file. Agents never see them.
Agents hold local tokens. The service holds only the SHA-256 hash of each one.
"""
from ops.control import approvals
from ops.fleetlib import config, httpc, httpd, log, paths, pause, secrets, tokens
from ops.publisher import adapters as adp
from ops.publisher.core import Publisher, PLATFORMS

LOG = log.get("publisher")


def guard_client(cfg):
    url = "http://127.0.0.1:%d/scan" % config.port(cfg, "guard")

    def scan(text):
        try:
            resp = httpc.post(url, json_body={"text": text}, timeout=60)
            data = resp.json()
            return data if resp.status == 200 and "label" in data else None
        except (httpc.TransportError, ValueError):
            return None
    return scan


def build(cfg, http=None):
    values = {}
    for entry in secrets.all_secrets():
        if entry["service"] == "publisher":
            values[entry["name"]] = secrets.value_of(entry["name"])
    adapters = {}
    for name in PLATFORMS:
        pcfg = config.get(cfg, "publisher.platforms.%s" % name, {}) or {}
        adapters[name] = adp.ADAPTERS[name](pcfg, values, http)
    return Publisher(cfg, adapters, guard_client(cfg), paths.ops_state_dir() / "publisher", paths.state_dir() / "outbox",
                     faq_root=paths.live_repo() / "projects", paused=pause.paused,
                     approved=approvals.is_approved)


def routes(publisher):
    def posts(req):
        return 200, publisher.post(req.json())

    def replies(req):
        return 200, publisher.reply(req.json())

    return {("POST", "/posts"): posts, ("POST", "/replies"): replies,
            ("GET", "/platforms"): lambda r: (200, publisher.platforms()),
            ("GET", "/health"): lambda r: (200, {"status": "OK", "mode": publisher.mode})}


def token_check(req, name):
    return httpd.verify_token(req.bearer(), tokens.hashes(name))


def main():
    cfg = config.load()
    publisher = build(cfg)
    handlers = routes(publisher)

    def guarded(name, handler):
        def run(req):
            if not token_check(req, name):
                raise httpd.HttpError(401, "a valid bearer token is required")
            return handler(req)
        return run
    handlers[("POST", "/posts")] = guarded("PUBLISHER_API_TOKEN", handlers[("POST", "/posts")])
    handlers[("POST", "/replies")] = guarded("PUBLISHER_REPLY_TOKEN", handlers[("POST", "/replies")])
    server = httpd.JsonServer("127.0.0.1", config.port(cfg, "publisher"), handlers, max_body=300_000, log=LOG)
    LOG.info("started", port=server.port, mode=publisher.mode)
    server.serve_forever()


if __name__ == "__main__":
    main()
