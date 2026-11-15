"""A small JSON HTTP server for the local services.

- It binds to loopback only.
- It checks a bearer token against stored SHA-256 hashes.
- It limits the body size. It never sends a stack trace to the client.
"""
import hashlib
import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

LOOPBACK = ("127.0.0.1", "::1", "localhost")


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def verify_token(provided, hashes):
    """True when the SHA-256 of `provided` equals one of the hashes. Constant-time compare."""
    if not provided or not hashes:
        return False
    digest = token_hash(provided)
    ok = False
    for h in hashes:
        if hmac.compare_digest(digest, h):
            ok = True
    return ok


class HttpError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class Request:
    def __init__(self, method, path, query, headers, body, client):
        self.method = method
        self.path = path
        self.query = query
        self.headers = headers
        self.body = body
        self.client = client
        self._json = None

    def json(self):
        if self._json is None:
            if not self.body:
                raise HttpError(400, "a JSON body is required")
            try:
                self._json = json.loads(self.body.decode("utf-8"))
            except ValueError:
                raise HttpError(400, "the body is not valid JSON") from None
        return self._json

    def bearer(self):
        value = self.headers.get("authorization", "")
        if value.lower().startswith("bearer "):
            return value[7:].strip()
        return ""


class JsonServer:
    def __init__(self, host, port, routes, auth_hashes=None, public_paths=("/health",),
                 max_body=1_000_000, log=None):
        if host not in LOOPBACK:
            raise ValueError("services must bind to loopback only, not %s" % host)
        self.routes = routes
        self.auth_hashes = auth_hashes
        self.public_paths = set(public_paths)
        self.max_body = max_body
        self.log = log
        outer = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "agent-fleet"
            sys_version = ""

            def log_message(self, *args):
                return

            def _send(self, status, obj):
                payload = json.dumps(obj, sort_keys=True).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(payload)

            def _handle(self):
                parsed = urlparse(self.path)
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    if length > outer.max_body:
                        raise HttpError(413, "the body is too large")
                    body = self.rfile.read(length) if length else b""
                    headers = {k.lower(): v for k, v in self.headers.items()}
                    req = Request(self.command, parsed.path, parse_qs(parsed.query), headers, body,
                                  self.client_address[0])
                    handler = outer.routes.get((self.command, parsed.path))
                    if handler is None:
                        raise HttpError(404, "not found")
                    if outer.auth_hashes is not None and parsed.path not in outer.public_paths:
                        if not verify_token(req.bearer(), outer.auth_hashes() if callable(outer.auth_hashes)
                                            else outer.auth_hashes):
                            raise HttpError(401, "a valid bearer token is required")
                    status, obj = handler(req)
                    self._send(status, obj)
                except HttpError as exc:
                    self._send(exc.status, {"error": exc.message})
                except Exception as exc:  # never leak details to the client
                    if outer.log:
                        outer.log.error("handler_failed", path=parsed.path, error=type(exc).__name__)
                    self._send(500, {"error": "internal error"})

            do_GET = do_POST = do_PUT = do_DELETE = _handle

        self.httpd = ThreadingHTTPServer((host, port), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self._thread = None

    def start_background(self):
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def serve_forever(self):
        self.httpd.serve_forever()

    def shutdown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
