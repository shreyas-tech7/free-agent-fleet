"""A safe page fetcher. Standard library only.

Safety rules:
 - Every URL goes through netguard.check_url. Every name goes through netguard.resolve_public.
 - The connection goes to the address that was checked, so DNS cannot change in between.
 - Redirects are followed by hand, at most 5. Each hop gets the same checks and a robots.txt check.
 - robots.txt must allow the fetch. A robots.txt that cannot be read (5xx or network error) means no.
 - The page must be text. The size and the time are capped.
 - A page or header that says "noai" is refused.
 - The same host is not hit more often than once per host_delay seconds.
 - The user agent says who we are. No fake browser string. No stealth.
"""
import http.client
import re
import socket
import ssl
import time
import urllib.parse
import urllib.robotparser

from . import netguard
from .netguard import Blocked

TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain", "text/markdown")
REDIRECT_CODES = (301, 302, 303, 307, 308)
READ_CHUNK = 65536


class Fetched:
    def __init__(self, url, status, content_type, body, truncated, headers):
        self.url = url
        self.status = status
        self.content_type = content_type
        self.body = body
        self.truncated = truncated
        self.headers = headers


class _Conn(http.client.HTTPConnection):
    def __init__(self, host, port, ip, timeout):
        super().__init__(host, port, timeout=timeout)
        self._ip = ip

    def connect(self):
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


class _SConn(http.client.HTTPSConnection):
    def __init__(self, host, port, ip, timeout):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._ip = ip

    def connect(self):
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class SafeFetcher:
    def __init__(self, user_agent="agent-fleet-reader/0.1", denylist=(), allow_private=False,
                 resolver=socket.getaddrinfo, timeout=15, max_bytes=2_000_000, max_redirects=5,
                 robots_ttl=3600, host_delay=2.0, clock=time.monotonic, sleep=time.sleep, total_timeout=60):
        self.total_timeout = total_timeout
        self.user_agent = user_agent
        self.token = user_agent.split("/")[0].strip().lower()
        self.denylist = set(denylist)
        self.allow_private = allow_private
        self.resolver = resolver
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self.robots_ttl = robots_ttl
        self.host_delay = host_delay
        self.clock = clock
        self.sleep = sleep
        self._robots = {}
        self._last = {}
        self._delays = {}

    # ---- one request to a checked address ----
    def _request(self, scheme, host, port, path, accept="text/html,text/plain;q=0.9,*/*;q=0.1"):
        ips = netguard.resolve_public(host, port, self.allow_private, self.resolver)
        delay = self.host_delay if path == "/robots.txt" else max(self.host_delay, self._delays.get(host, 0))
        wait = delay - (self.clock() - self._last.get(host, -1e9))
        if wait > 0:
            self.sleep(min(wait, delay))
        last_error = None
        started = self.clock()
        for ip in ips[:3]:
            cls = _SConn if scheme == "https" else _Conn
            conn = cls(host, port, ip, self.timeout)
            try:
                conn.request("GET", path, headers={
                    "User-Agent": self.user_agent, "Accept": accept,
                    "Accept-Encoding": "identity", "Connection": "close"})
                resp = conn.getresponse()
                body = self._read_body(resp, started)
                truncated = len(body) > self.max_bytes
                self._last[host] = self.clock()
                return resp.status, {k.lower(): v for k, v in resp.getheaders()}, body[:self.max_bytes], truncated
            except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
                last_error = exc
            finally:
                conn.close()
        raise Blocked("network_error", type(last_error).__name__)

    def _read_body(self, resp, started):
        """Read at most max_bytes + 1 bytes, in pieces, inside the total time limit.

        The socket timeout limits one read. A server that sends one byte every few seconds would pass it for hours
        and hold one of the two reader slots. The total limit stops that.
        """
        chunks, size = [], 0
        while size <= self.max_bytes:
            if self.clock() - started > self.total_timeout:
                raise Blocked("timeout", "the page took longer than %d seconds" % self.total_timeout)
            piece = resp.read(min(READ_CHUNK, self.max_bytes + 1 - size))
            if not piece:
                break
            chunks.append(piece)
            size += len(piece)
        return b"".join(chunks)

    # ---- robots.txt ----
    def _robots_for(self, scheme, host, port):
        key = (scheme, host, port)
        cached = self._robots.get(key)
        now = self.clock()
        if cached and cached[1] > now:
            return cached[0]
        parser = urllib.robotparser.RobotFileParser()
        base = "%s://%s%s" % (scheme, host, "" if port in (80, 443) else ":%d" % port)
        try:
            status, _h, body, _t = self._request(scheme, host, port, "/robots.txt", accept="text/plain,*/*;q=0.1")
        except Blocked as exc:
            if exc.reason in ("private_address", "dns_failed", "denylist"):
                raise
            parser = None                        # unreadable means no
        else:
            if 200 <= status < 300:
                parser.parse(body.decode("utf-8", "replace").splitlines())
                crawl_delay = parser.crawl_delay(self.token) or parser.crawl_delay("*")
                if crawl_delay:
                    self._delays[host] = min(float(crawl_delay), 60.0)
            elif 400 <= status < 500:
                parser.parse([])                 # no robots.txt means everything is allowed
            else:
                parser = None                    # 5xx means no
        self._robots[key] = (parser, now + self.robots_ttl)
        return parser

    def robots_allows(self, scheme, host, port, path):
        parser = self._robots_for(scheme, host, port)
        if parser is None:
            return False
        return parser.can_fetch(self.token, path)

    # ---- the public call ----
    def fetch(self, url):
        hops = 0
        current = url
        while True:
            scheme, host, port, path = netguard.check_url(current, self.denylist, self.allow_private)
            if not self.robots_allows(scheme, host, port, path):
                raise Blocked("robots_txt", host)
            status, headers, body, truncated = self._request(scheme, host, port, path)
            if status in REDIRECT_CODES and headers.get("location"):
                hops += 1
                if hops > self.max_redirects:
                    raise Blocked("too_many_redirects")
                current = urllib.parse.urljoin(current, headers["location"])
                continue
            if status >= 400:
                raise Blocked("http_error", str(status))
            ctype = headers.get("content-type", "").split(";")[0].strip().lower()
            if ctype not in TEXT_TYPES:
                raise Blocked("not_text", ctype or "unknown")
            if "noai" in headers.get("x-robots-tag", "").lower():
                raise Blocked("noai")
            if ctype in ("text/html", "application/xhtml+xml") and re.search(
                    rb"<meta[^>]+name=[\"']robots[\"'][^>]*content=[\"'][^\"']*noai", body[:50000], re.I):
                raise Blocked("noai")
            return Fetched(current, status, ctype, body, truncated, headers)
