"""A small HTTP client for the services.

- It never follows redirects. A redirect could send a token to another host.
- It ignores proxy settings in the environment (rule R4: no proxy features).
- Tests replace the transport with set_transport().
"""
import json as _json
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "agent-fleet/0.1"


class TransportError(Exception):
    pass


class Response:
    def __init__(self, status, headers, body):
        self.status = status
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.body = body

    def text(self):
        return self.body.decode("utf-8", "replace")

    def json(self):
        return _json.loads(self.body.decode("utf-8", "replace"))

    @property
    def ok(self):
        return 200 <= self.status < 300


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def _default_transport(method, url, headers, body, timeout, max_bytes):
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            data = resp.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise TransportError("response too large")
            return Response(resp.status, dict(resp.headers), data)
    except urllib.error.HTTPError as exc:
        data = exc.read(max_bytes + 1) if exc.fp else b""
        return Response(exc.code, dict(exc.headers or {}), data[:max_bytes])
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise TransportError(str(getattr(exc, "reason", exc))) from None


_transport = _default_transport


def set_transport(fn):
    global _transport
    _transport = fn


def reset_transport():
    global _transport
    _transport = _default_transport


def request(method, url, headers=None, json_body=None, data=None, timeout=15, max_bytes=5_000_000):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise TransportError("only http and https URLs are allowed")
    hdrs = {"User-Agent": USER_AGENT}
    hdrs.update(headers or {})
    body = None
    if json_body is not None:
        body = _json.dumps(json_body).encode()
        hdrs.setdefault("Content-Type", "application/json")
    elif data is not None:
        body = data if isinstance(data, bytes) else data.encode()
    return _transport(method.upper(), url, hdrs, body, timeout, max_bytes)


def get(url, **kw):
    return request("GET", url, **kw)


def post(url, **kw):
    return request("POST", url, **kw)
