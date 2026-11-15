"""Chat channels for the control bot: Telegram and Discord. Official APIs, standard library only."""
import time

from ops.fleetlib import httpc


class ChannelError(Exception):
    pass


def split_text(text, limit):
    """Cut a long reply into parts that fit the platform limit, at line ends when possible."""
    text = text or ""
    parts = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        cut = cut if cut > limit // 2 else limit
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    parts.append(text)
    return parts


class Telegram:
    name = "telegram"
    limit = 4000

    def __init__(self, token, http=None, owner_ids=()):
        self._token = token
        self.http = http or httpc
        self.owner_ids = {str(i) for i in owner_ids}
        self.offset = 0

    def ready(self):
        return bool(self._token) and bool(self.owner_ids)

    def _call(self, method, body, timeout=40):
        try:
            resp = self.http.request("POST", "https://api.telegram.org/bot%s/%s" % (self._token, method), json_body=body, timeout=timeout)
            data = resp.json()
        except (httpc.TransportError, ValueError):
            raise ChannelError("telegram is not reachable") from None
        if resp.status != 200 or not data.get("ok"):
            raise ChannelError("telegram answered with status %d" % resp.status)
        return data.get("result")

    def poll(self, wait=25):
        """Return a list of (user_id, reply_target, text). Only private chats count."""
        updates = self._call("getUpdates", {"offset": self.offset, "timeout": wait, "allowed_updates": ["message"]}, timeout=wait + 15) or []
        out = []
        for u in updates:
            self.offset = max(self.offset, u.get("update_id", 0) + 1)
            msg = u.get("message") or {}
            chat = msg.get("chat") or {}
            if chat.get("type") != "private" or not msg.get("text"):
                continue
            out.append((str((msg.get("from") or {}).get("id", "")), str(chat.get("id")), msg["text"]))
        return out

    def send(self, target, text):
        for part in split_text(text, self.limit):
            self._call("sendMessage", {"chat_id": target, "text": part, "disable_web_page_preview": True})

    def notify_owner(self, text):
        for owner in sorted(self.owner_ids):
            self.send(owner, text)


class Discord:
    name = "discord"
    limit = 1900

    def __init__(self, token, http=None, owner_ids=(), channel_id=""):
        self._token = token
        self.http = http or httpc
        self.owner_ids = {str(i) for i in owner_ids}
        self.channel_id = str(channel_id or "")
        self._dm = {}
        self._last = {}

    def ready(self):
        return bool(self._token) and (bool(self.owner_ids) or bool(self.channel_id))

    def _call(self, method, path, body=None):
        try:
            resp = self.http.request(method, "https://discord.com/api/v10" + path, json_body=body,
                                     headers={"Authorization": "Bot " + self._token}, timeout=30)
            data = resp.json() if resp.body else {}
        except (httpc.TransportError, ValueError):
            raise ChannelError("discord is not reachable") from None
        if resp.status >= 300:
            raise ChannelError("discord answered with status %d" % resp.status)
        return data

    def _dm_channel(self, owner):
        if owner not in self._dm:
            self._dm[owner] = self._call("POST", "/users/@me/channels", {"recipient_id": owner})["id"]
        return self._dm[owner]

    def _channels(self):
        chans = {self._dm_channel(o): "dm" for o in sorted(self.owner_ids)}
        if self.channel_id:
            chans[self.channel_id] = "channel"
        return chans

    def poll(self):
        """Return a list of (user_id, reply_target, text) from the owner DMs and the optional control channel."""
        out = []
        for channel in self._channels():
            path = "/channels/%s/messages?limit=20" % channel
            if channel in self._last:
                path += "&after=" + self._last[channel]
            messages = self._call("GET", path) or []
            if channel not in self._last:
                # First look at a channel: remember the newest message and do not act on old ones.
                self._last[channel] = max((m["id"] for m in messages), key=int, default="0")
                continue
            for m in sorted(messages, key=lambda m: int(m["id"])):
                self._last[channel] = m["id"]
                if (m.get("author") or {}).get("bot") or not m.get("content"):
                    continue
                out.append((str(m["author"]["id"]), channel, m["content"]))
        return out

    def send(self, target, text):
        for part in split_text(text, self.limit):
            self._call("POST", "/channels/%s/messages" % target, {"content": part, "allowed_mentions": {"parse": []}})

    def notify_owner(self, text):
        for owner in sorted(self.owner_ids):
            self.send(self._dm_channel(owner), text)
