"""A small Slack Web API client over httpc. The control bot posts with it, and `fleetctl slack bootstrap` sets up with it. See D-052.

Slack answers HTTP 200 with {"ok": false, "error": "..."} for most errors. A 429 carries a Retry-After header.
Posts to one channel go out at most once a second. The token goes only in the Authorization header.
An error never holds the token or the text of a message.
"""
import time

from ops.fleetlib import httpc

API = "https://slack.com/api/"
# The bot scopes that each method needs. The manifest of Fleet HQ holds exactly these scopes, plus `commands` for /fleet and
# `chat:write.customize` for the agent names and icons. A test checks it. Keep only the scopes that the code uses.
SCOPES_FOR = {"chat.postMessage": ["chat:write"], "chat.update": ["chat:write"], "views.publish": [], "conversations.list": ["channels:read"],
              "conversations.create": ["channels:manage"], "conversations.setTopic": ["channels:manage"], "conversations.join": ["channels:join"],
              "conversations.invite": ["channels:manage"], "pins.add": ["pins:write"], "bookmarks.add": ["bookmarks:write"]}
EXTRA_SCOPES = ["commands", "chat:write.customize"]
MAX_THROTTLE = 5.0


class SlackApiError(Exception):
    def __init__(self, error, retry_after=0):
        super().__init__(error)
        self.error, self.retry_after = error, retry_after


class SlackApi:
    def __init__(self, token, request=None, clock=time.monotonic, sleep=time.sleep, min_interval=1.0):
        self._token = token
        self._request = request or httpc.request
        self.clock, self.sleep, self.min_interval = clock, sleep, min_interval
        self._last = {}

    def call(self, method, **params):
        try:
            resp = self._request("POST", API + method, headers={"Authorization": "Bearer " + self._token}, json_body=params, timeout=20)
        except httpc.TransportError:
            raise SlackApiError("unreachable") from None
        if resp.status == 429:
            raise SlackApiError("ratelimited", int(resp.headers.get("retry-after", "1") or 1))
        if resp.status >= 400:
            raise SlackApiError("http_%d" % resp.status)
        try:
            data = resp.json()
        except ValueError:
            raise SlackApiError("not_json") from None
        if not data.get("ok"):
            raise SlackApiError(str(data.get("error", "unknown"))[:60])
        return data

    def _throttle(self, channel):
        """One post a second for each channel. It waits at most a few seconds, so a burst never blocks a caller for long."""
        last = self._last.get(channel)
        now = self.clock()
        if last is not None and now - last < self.min_interval:
            self.sleep(min(self.min_interval - (now - last), MAX_THROTTLE))
        self._last[channel] = self.clock()

    # ---- messages
    def post(self, channel, text, blocks=None, username=None, icon_emoji=None, thread_ts=None):
        self._throttle(channel)
        params = {"channel": channel, "text": text, "unfurl_links": False, "unfurl_media": False}
        for key, value in (("blocks", blocks), ("username", username), ("icon_emoji", icon_emoji), ("thread_ts", thread_ts)):
            if value:
                params[key] = value
        return self.call("chat.postMessage", **params)

    def update(self, channel, ts, text, blocks=None):
        params = {"channel": channel, "ts": ts, "text": text}
        if blocks:
            params["blocks"] = blocks
        return self.call("chat.update", **params)

    def publish_home(self, user_id, view):
        return self.call("views.publish", user_id=user_id, view=view)

    # ---- channels (used by bootstrap)
    def channels(self):
        """{name: id} of the public channels that the bot can see."""
        found, cursor = {}, ""
        while True:
            data = self.call("conversations.list", types="public_channel", exclude_archived=True, limit=200, **({"cursor": cursor} if cursor else {}))
            for ch in data.get("channels", []):
                found[ch["name"]] = ch["id"]
            cursor = (data.get("response_metadata") or {}).get("next_cursor", "")
            if not cursor:
                return found

    def create_channel(self, name):
        """Make a public channel. Returns its ID. A name that exists returns the existing ID."""
        try:
            return self.call("conversations.create", name=name, is_private=False)["channel"]["id"]
        except SlackApiError as exc:
            if exc.error == "name_taken":
                found = self.channels().get(name)
                if found:
                    return found
            raise

    def set_topic(self, channel_id, topic):
        return self.call("conversations.setTopic", channel=channel_id, topic=topic[:250])

    def join(self, channel_id):
        return self.call("conversations.join", channel=channel_id)

    def invite(self, channel_id, user_ids):
        try:
            return self.call("conversations.invite", channel=channel_id, users=",".join(user_ids))
        except SlackApiError as exc:
            if exc.error in ("already_in_channel", "cant_invite_self"):
                return {"ok": True}
            raise

    def pin(self, channel_id, ts):
        return self.call("pins.add", channel=channel_id, timestamp=ts)

    def bookmark(self, channel_id, title, link):
        return self.call("bookmarks.add", channel_id=channel_id, title=title, type="link", link=link)
