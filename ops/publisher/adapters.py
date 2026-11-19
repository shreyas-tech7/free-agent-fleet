"""Platform adapters. Each one speaks the official API of one platform.

Rules (core/RULES.md, rule 6):
 - Official APIs only. No browser automation of a user account.
 - Each adapter sets the platform's automated-account label where the platform asks for one.
 - An adapter that is off, or not ready, never sends.
 - A key never appears in a log line, an error text, or a result.
"""
import datetime
import re
import time
import urllib.parse

from ops.fleetlib import httpc, redact


AT_POST_URI = re.compile(r"^at://[A-Za-z0-9.:%_-]{3,253}/app\.bsky\.feed\.post/[A-Za-z0-9._~:-]{1,64}$")


class AdapterError(Exception):
    pass


class AdapterOff(AdapterError):
    pass


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class Adapter:
    name = ""
    secret_names = ()
    needs = ()                  # config keys that must be set

    def __init__(self, cfg, secret_values, http=None):
        self.cfg = cfg
        self.secrets = secret_values
        self.http = http or httpc
        self.max_chars = int(cfg.get("max_chars", 1000))

    def ready(self):
        missing = [s for s in self.secret_names if not self.secrets.get(s)]
        missing += [k for k in self.needs if not self.cfg.get(k)]
        return (not missing, "missing: " + ", ".join(missing) if missing else "ok")

    def ensure_label(self):
        """Set the automated-account label if the platform has one. Safe to call twice."""

    def post(self, text, title=None, tags=None, reply_to=None, media=None):
        raise NotImplementedError

    def _call(self, method, url, **kw):
        try:
            resp = self.http.request(method, url, **kw)
        except httpc.TransportError as exc:
            raise AdapterError("%s is not reachable: %s" % (self.name, redact.redact_text(str(exc)))) from None
        if not (200 <= resp.status < 300):
            raise AdapterError("%s answered with status %d" % (self.name, resp.status))
        try:
            return resp.json() if resp.body else {}
        except ValueError:
            return {}


# ---------------------------------------------------------------- Bluesky (AT Protocol)
class Bluesky(Adapter):
    name = "bluesky"
    secret_names = ("BLUESKY_APP_PASSWORD",)
    needs = ("handle",)

    def __init__(self, cfg, secret_values, http=None, clock=time.time):
        super().__init__(cfg, secret_values, http)
        self.pds = cfg.get("pds_url", "https://bsky.social").rstrip("/")
        self.clock = clock
        self._session = None
        self._session_at = 0

    def _login(self):
        if self._session and self.clock() - self._session_at < 3300:
            return self._session
        data = self._call("POST", self.pds + "/xrpc/com.atproto.server.createSession",
                          json_body={"identifier": self.cfg["handle"], "password": self.secrets["BLUESKY_APP_PASSWORD"]})
        if not data.get("accessJwt") or not data.get("did"):
            raise AdapterError("bluesky login gave no session")
        self._session, self._session_at = data, self.clock()
        return data

    def _auth(self):
        return {"Authorization": "Bearer " + self._login()["accessJwt"]}

    def ensure_label(self):
        """Bluesky recommends a profile self-label {val: 'bot'} for automated accounts."""
        did = self._login()["did"]
        record, cid = {"$type": "app.bsky.actor.profile"}, None
        try:
            got = self._call("GET", "%s/xrpc/com.atproto.repo.getRecord?repo=%s&collection=app.bsky.actor.profile&rkey=self" % (self.pds, did),
                             headers=self._auth())
            record, cid = got.get("value", record), got.get("cid")
        except AdapterError:
            pass
        values = [v.get("val") for v in (record.get("labels") or {}).get("values", [])]
        if "bot" in values:
            return
        record["labels"] = {"$type": "com.atproto.label.defs#selfLabels", "values": [{"val": "bot"}]}
        body = {"repo": did, "collection": "app.bsky.actor.profile", "rkey": "self", "record": record}
        if cid:
            body["swapRecord"] = cid
        self._call("POST", self.pds + "/xrpc/com.atproto.repo.putRecord", headers=self._auth(), json_body=body)

    @staticmethod
    def facets(text):
        """Make links clickable. Bluesky counts UTF-8 bytes."""
        out = []
        for m in re.finditer(r"https?://[^\s)\]]+", text):
            start = len(text[:m.start()].encode())
            out.append({"index": {"byteStart": start, "byteEnd": start + len(m.group(0).encode())},
                        "features": [{"$type": "app.bsky.richtext.facet#link", "uri": m.group(0)}]})
        return out

    @staticmethod
    def _ref(node):
        """A strong reference {uri, cid}, or None when the node is not one."""
        if isinstance(node, dict) and isinstance(node.get("uri"), str) and isinstance(node.get("cid"), str) \
                and AT_POST_URI.match(node["uri"]) and node["cid"]:
            return {"uri": node["uri"], "cid": node["cid"]}
        return None

    def _reply_refs(self, reply_to):
        """The root and the parent of a reply. Bluesky needs the address and the content hash of both.

        The Publisher passes the address of the post as a string. The adapter asks Bluesky for its hash and its thread
        root. A post that is itself a reply names the root of its thread. Any other post is its own root.
        """
        parent = self._ref(reply_to)
        if parent is not None:
            return {"root": self._ref(reply_to.get("root")) or parent, "parent": parent}
        if not isinstance(reply_to, str) or not AT_POST_URI.match(reply_to):
            raise AdapterError("bluesky needs the at:// address of a post to reply to")
        got = self._call("GET", "%s/xrpc/app.bsky.feed.getPosts?uris=%s" % (self.pds, urllib.parse.quote(reply_to, safe="")),
                         headers=self._auth())
        posts = got.get("posts") if isinstance(got, dict) else None
        found = posts[0] if isinstance(posts, list) and posts and isinstance(posts[0], dict) else {}
        parent = self._ref(found)
        if parent is None:
            raise AdapterError("bluesky could not find the post to reply to")
        thread = (found.get("record") or {}).get("reply") if isinstance(found.get("record"), dict) else None
        return {"root": self._ref((thread or {}).get("root")) or parent, "parent": parent}

    def post(self, text, title=None, tags=None, reply_to=None, media=None):
        did = self._login()["did"]
        record = {"$type": "app.bsky.feed.post", "text": text, "createdAt": _now(), "langs": ["en"]}
        facets = self.facets(text)
        if facets:
            record["facets"] = facets
        if reply_to:
            record["reply"] = self._reply_refs(reply_to)
        data = self._call("POST", self.pds + "/xrpc/com.atproto.repo.createRecord", headers=self._auth(),
                          json_body={"repo": did, "collection": "app.bsky.feed.post", "record": record})
        return {"id": data.get("uri", ""), "cid": data.get("cid", "")}


# ---------------------------------------------------------------- Mastodon
class Mastodon(Adapter):
    name = "mastodon"
    secret_names = ("MASTODON_ACCESS_TOKEN",)
    needs = ("instance_url",)

    def _h(self, extra=None):
        h = {"Authorization": "Bearer " + self.secrets["MASTODON_ACCESS_TOKEN"]}
        h.update(extra or {})
        return h

    def ensure_label(self):
        """Many instances require the bot flag on an automated account."""
        base = self.cfg["instance_url"].rstrip("/")
        me = self._call("GET", base + "/api/v1/accounts/verify_credentials", headers=self._h())
        if not me.get("bot") and (self.cfg.get("label") or {}).get("set_bot_flag", True):
            self._call("PATCH", base + "/api/v1/accounts/update_credentials", headers=self._h(), json_body={"bot": True})

    def post(self, text, title=None, tags=None, reply_to=None, media=None):
        base = self.cfg["instance_url"].rstrip("/")
        body = {"status": text, "visibility": "public", "language": "en"}
        if reply_to:
            body["in_reply_to_id"] = reply_to if isinstance(reply_to, str) else reply_to.get("id")
        data = self._call("POST", base + "/api/v1/statuses", headers=self._h({"Idempotency-Key": "fleet-%d" % int(time.time() * 1000)}),
                          json_body=body)
        return {"id": str(data.get("id", "")), "url": data.get("url", "")}


# ---------------------------------------------------------------- Discord (a bot in the fleet's own server)
class Discord(Adapter):
    name = "discord"
    secret_names = ("DISCORD_PUBLISHER_BOT_TOKEN",)
    needs = ("channel_id",)

    def post(self, text, title=None, tags=None, reply_to=None, media=None):
        data = self._call("POST", "https://discord.com/api/v10/channels/%s/messages" % self.cfg["channel_id"],
                          headers={"Authorization": "Bot " + self.secrets["DISCORD_PUBLISHER_BOT_TOKEN"]},
                          json_body={"content": text, "allowed_mentions": {"parse": []}})
        return {"id": str(data.get("id", ""))}


# ---------------------------------------------------------------- Telegram channel
class Telegram(Adapter):
    name = "telegram"
    secret_names = ("TELEGRAM_PUBLISHER_BOT_TOKEN",)
    needs = ("channel_id",)

    def post(self, text, title=None, tags=None, reply_to=None, media=None):
        # The token is in the URL path. The error text must never carry the URL.
        url = "https://api.telegram.org/bot%s/sendMessage" % self.secrets["TELEGRAM_PUBLISHER_BOT_TOKEN"]
        data = self._call("POST", url, json_body={"chat_id": self.cfg["channel_id"], "text": text, "disable_web_page_preview": False})
        return {"id": str((data.get("result") or {}).get("message_id", ""))}


# ---------------------------------------------------------------- dev.to (Forem API)
class DevTo(Adapter):
    name = "devto"
    secret_names = ("DEVTO_API_KEY",)

    def post(self, text, title=None, tags=None, reply_to=None, media=None):
        if not title:
            raise AdapterError("devto needs a title")
        article = {"title": title[:128], "body_markdown": text, "published": True, "tags": (tags or [])[:4]}
        data = self._call("POST", "https://dev.to/api/articles", headers={"api-key": self.secrets["DEVTO_API_KEY"]},
                          json_body={"article": article})
        return {"id": str(data.get("id", "")), "url": data.get("url", "")}


# ---------------------------------------------------------------- GitHub (release notes)
class GitHub(Adapter):
    name = "github"
    secret_names = ("GITHUB_PUBLISHER_TOKEN",)
    needs = ("repo",)

    def post(self, text, title=None, tags=None, reply_to=None, media=None):
        tag = (tags or [time.strftime("v%Y.%m.%d-%H%M")])[0]
        data = self._call("POST", "https://api.github.com/repos/%s/releases" % self.cfg["repo"],
                          headers={"Authorization": "Bearer " + self.secrets["GITHUB_PUBLISHER_TOKEN"],
                                   "Accept": "application/vnd.github+json"},
                          json_body={"tag_name": tag, "name": title or tag, "body": text})
        return {"id": str(data.get("id", "")), "url": data.get("html_url", "")}


# ---------------------------------------------------------------- X (off until API access exists)
class X(Adapter):
    name = "x"
    secret_names = ("X_ACCESS_TOKEN",)

    def ready(self):
        ok, why = super().ready()
        if ok and (self.cfg.get("label") or {}).get("automated_label") == "required" and not self.cfg.get("automated_label_confirmed"):
            return False, "the owner must confirm the automated label in the X account settings (automated_label_confirmed)"
        return ok, why

    def post(self, text, title=None, tags=None, reply_to=None, media=None):
        data = self._call("POST", "https://api.x.com/2/tweets", headers={"Authorization": "Bearer " + self.secrets["X_ACCESS_TOKEN"]},
                          json_body={"text": text})
        return {"id": str((data.get("data") or {}).get("id", ""))}


# ---------------------------------------------------------------- Reddit (off until API access exists)
class Reddit(Adapter):
    name = "reddit"
    secret_names = ("REDDIT_CLIENT_SECRET",)

    def ready(self):
        return False, "the Reddit adapter stays off until Reddit API access exists"

    def post(self, text, title=None, tags=None, reply_to=None, media=None):
        raise AdapterOff("the Reddit adapter stays off until Reddit API access exists")


ADAPTERS = {c.name: c for c in (Bluesky, Mastodon, Discord, Telegram, DevTo, GitHub, X, Reddit)}
