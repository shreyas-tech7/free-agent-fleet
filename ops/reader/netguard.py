"""Network safety for the page reader (SSRF protection).

The reader must never fetch an internal address. That covers loopback, private ranges, link-local
(including the cloud metadata address), and anything that is not a global address.
check_url() looks at the URL. resolve_public() looks at the address behind the name.
The fetcher then connects to the checked address, so a name cannot change meaning in between.
"""
import ipaddress
import pathlib
import socket
import urllib.parse

ALLOWED_PORTS = {80, 443}


class Blocked(Exception):
    def __init__(self, reason, detail=""):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def load_denylist(path):
    domains = set()
    try:
        for line in pathlib.Path(path).read_text().splitlines():
            line = line.strip().lower()
            if line and not line.startswith("#"):
                domains.add(line)
    except OSError:
        pass
    return domains


def denied(host, denylist):
    host = host.lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in denylist)


def is_public_ip(ip):
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_global and not addr.is_multicast


def check_url(url, denylist=(), allow_private=False, allowed_ports=ALLOWED_PORTS):
    """Return (scheme, host, port, path_and_query). Raise Blocked for an unsafe URL."""
    if not isinstance(url, str) or len(url) > 2048:
        raise Blocked("bad_url")
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme not in ("http", "https"):
        raise Blocked("bad_scheme")
    if parts.username or parts.password:
        raise Blocked("credentials_in_url")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise Blocked("bad_url")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        raise Blocked("bad_url") from None
    if not allow_private and port not in allowed_ports:
        raise Blocked("bad_port")
    if denied(host, denylist):
        raise Blocked("denylist", host)
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and not allow_private and not is_public_ip(host):
        raise Blocked("private_address")
    if host in ("localhost", "localhost.localdomain") or host.endswith((".local", ".internal", ".localhost")):
        if not allow_private:
            raise Blocked("private_address")
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return parts.scheme, host, port, path


def resolve_public(host, port, allow_private=False, resolver=socket.getaddrinfo):
    """Return a list of IP strings. Every address must be global. Otherwise raise Blocked."""
    try:
        ipaddress.ip_address(host)
        addresses = [host]
    except ValueError:
        try:
            infos = resolver(host, port, type=socket.SOCK_STREAM)
        except OSError:
            raise Blocked("dns_failed", host) from None
        addresses = []
        for info in infos:
            ip = info[4][0]
            if ip not in addresses:
                addresses.append(ip)
    if not addresses:
        raise Blocked("dns_failed", host)
    if not allow_private:
        for ip in addresses:
            if not is_public_ip(ip):
                raise Blocked("private_address", host)
    return addresses
