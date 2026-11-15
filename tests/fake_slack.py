"""A fake Slack for tests. It records each Web API call and answers like Slack does. No network."""
import json

from ops.fleetlib import httpc


class FakeSlack:
    """Use as the `request` argument of SlackApi. `fail` maps a method to the error that Slack should answer with."""

    def __init__(self, fail=None, channels=None):
        self.calls = []                      # [(method, params)]
        self.fail = dict(fail or {})
        self.channels = dict(channels or {})      # name -> id
        self.counter = 0
        self.headers_seen = []

    def __call__(self, method, url, headers=None, json_body=None, timeout=0, **kw):
        name = url.rsplit("/", 1)[-1]
        params = dict(json_body or {})
        self.calls.append((name, params))
        self.headers_seen.append(dict(headers or {}))
        if name in self.fail:
            err = self.fail[name]
            if err == "429":
                return httpc.Response(429, {"retry-after": "3"}, b"")
            return httpc.Response(200, {}, json.dumps({"ok": False, "error": err}).encode())
        return httpc.Response(200, {}, json.dumps(self.answer(name, params)).encode())

    def answer(self, name, params):
        self.counter += 1
        if name == "conversations.list":
            return {"ok": True, "channels": [{"name": n, "id": i} for n, i in self.channels.items()], "response_metadata": {"next_cursor": ""}}
        if name == "conversations.create":
            if params["name"] in self.channels:
                return {"ok": False, "error": "name_taken"}
            cid = "C0%09d" % self.counter
            self.channels[params["name"]] = cid
            return {"ok": True, "channel": {"id": cid, "name": params["name"]}}
        if name == "chat.postMessage":
            return {"ok": True, "channel": params["channel"], "ts": "1700000000.%06d" % self.counter}
        return {"ok": True}

    def methods(self):
        return [n for n, _p in self.calls]

    def params(self, method):
        return [p for n, p in self.calls if n == method]
