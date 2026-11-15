"""The deny list for the public export. See D-051.

`fleetctl public terms` builds ~/.config/agent-fleet/private-terms.txt (mode 600, outside both repositories).
It holds what must never appear in the public repository: names, emails, the host name, addresses, home paths,
chat IDs, handles, domains, and project names. It prints only a count. Nothing here is ever written to the repository.
"""
import getpass
import json
import os
import pathlib
import pwd
import re
import socket
import subprocess

import yaml

STOP = {"debian", "ubuntu", "localhost", "laptop", "server", "linux", "user", "admin", "root", "owner", "example", "test", "fleet", "fleetops", "agent",
        "agents", "merger", "build", "claude", "sonnet", "main", "master", "dry-run", "live", "small", "medium", "large", "true", "false", "none", "slack",
        "telegram", "discord", "email", "github", "your", "name"}
SYSTEM_IDENTITY = re.compile(r"^agent-fleet\b|\bagent-fleet\b", re.I)                  # the fleet's own commit identities are not private
GENERIC_EMAIL = re.compile(r"(noreply|\.invalid$|\.example$|\.test$|@example\.(com|org|net)$|@localhost$)", re.I)
SAFE_VALUES = {"america/chicago"}
SAFE_PATTERN = re.compile(r"^fleet-[a-z]+$")                  # the virtual model names are public
TIME_OR_SMALL = re.compile(r"^(\d{1,2}:\d{2}|\d{1,6})$")
GENERIC_PROJECT = re.compile(r"^project-\d+$|^dry-run-")


def terms_path(home=None):
    home = pathlib.Path(home or pwd.getpwnam(os.environ.get("SUDO_USER") or getpass.getuser()).pw_dir)
    return home / ".config" / "agent-fleet" / "private-terms.txt"


def _run(runner, argv, timeout=30):
    try:
        proc = runner(argv, capture_output=True, text=True, timeout=timeout)  # nosemgrep: dangerous-subprocess-use-audit
        return proc.stdout if proc.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _add(found, value):
    value = str(value or "").strip()
    if len(value) >= 4 and value.lower() not in STOP and value.lower() not in SAFE_VALUES and not SAFE_PATTERN.match(value.lower()):
        found.add(value)


def _name_parts(found, name):
    _add(found, name)
    for token in re.split(r"[\s._-]+", str(name or "")):
        _add(found, token)


def _leaves(node, found):
    if isinstance(node, dict):
        for value in node.values():
            _leaves(value, found)
    elif isinstance(node, list):
        for value in node:
            _leaves(value, found)
    elif isinstance(node, bool) or node is None:
        return
    elif isinstance(node, int):
        if node >= 1_000_000:                       # a chat or user ID. Ports and limits are small.
            found.add(str(node))
    elif isinstance(node, str):
        if TIME_OR_SMALL.match(node):
            return                                   # a time such as 07:00, or a small number. A chat ID has more digits.
        _add(found, node)
        if "@" in node:
            _add(found, node.split("@")[0])


# The live config holds many settings that are public: provider names, modes, limits, quiet hours. A term from them would flag the public tree for no reason.
# Only these paths carry personal data by design. The private owner file is read in full, because everything in it is personal. See D-059.
PRIVATE_PATHS = ("owner", "briefing.email", "publisher.platforms", "backup.github_remote", "slack.channels", "control", "deployer", "cloudflare")


def _private_leaves(node, found, path=""):
    """The leaves of the live config overrides that sit under a personal path."""
    if isinstance(node, dict):
        for key, value in node.items():
            _private_leaves(value, found, "%s.%s" % (path, key) if path else str(key))
    elif any(path == p or path.startswith(p + ".") for p in PRIVATE_PATHS):
        _leaves(node, found)


def collect(repo, home=None, runner=subprocess.run, overrides=None, owner_file=None, extra_file=None):
    """Return the set of terms. Every source is optional. A source that fails adds nothing."""
    found = set()
    repo = pathlib.Path(repo)
    # git: names and emails of every author, and the local settings
    log = _run(runner, ["git", "-C", str(repo), "log", "--format=%an%n%ae%n%cn%n%ce"])
    for line in set(log.split("\n")):
        if "@" in line:
            if not GENERIC_EMAIL.search(line):
                _add(found, line)
                _add(found, line.split("@")[0])
        elif not SYSTEM_IDENTITY.search(line):
            _name_parts(found, line)
    for key in ("user.name", "user.email"):
        value = _run(runner, ["git", "-C", str(repo), "config", key]).strip()
        _name_parts(found, value) if key == "user.name" else (_add(found, value), _add(found, value.split("@")[0]))
    # the account, the home folder, the host
    user = os.environ.get("SUDO_USER") or getpass.getuser()
    _add(found, user)
    try:
        _add(found, pwd.getpwnam(user).pw_dir)
    except KeyError:
        pass
    _add(found, socket.gethostname())
    _add(found, socket.getfqdn())
    # addresses
    for row in json.loads(_run(runner, ["ip", "-j", "addr"]) or "[]"):
        for addr in row.get("addr_info", []):
            ip = addr.get("local", "")
            if ip and not ip.startswith(("127.", "::1", "fe80")):
                found.add(ip)
    for ip in _run(runner, ["tailscale", "ip"]).split():
        found.add(ip)
    try:
        status = json.loads(_run(runner, ["tailscale", "status", "--json"]) or "{}")
        _add(found, status.get("MagicDNSSuffix"))
        _add(found, (status.get("Self") or {}).get("DNSName", "").rstrip("."))
    except ValueError:
        pass
    # the GitHub login
    _add(found, _run(runner, ["gh", "api", "user", "--jq", ".login"]).strip())
    # values that the owner set in the live config and in the private owner file
    if isinstance(overrides, dict):
        _private_leaves(overrides, found)
    if isinstance(owner_file, dict):
        _leaves(owner_file, found)
    # project names and the brand kit
    projects = repo / "projects"
    if projects.is_dir():
        for entry in projects.iterdir():
            if entry.is_dir() and not entry.name.startswith(("_", ".")) and not GENERIC_PROJECT.match(entry.name):
                _add(found, entry.name)
        brand = projects / "_studio" / "brand"
        if brand.is_dir():
            for path in brand.glob("*"):
                if path.suffix in (".yaml", ".yml", ".json", ".md") and path.is_file() and path.stat().st_size < 100_000:
                    for match in re.finditer(r"(?im)^\W*(?:name|brand|studio)\s*[:=]\s*[\"']?([^\n\"']{4,60})", path.read_text(errors="replace")):
                        _name_parts(found, match.group(1))
    # Windows names that appear in the repository text
    for path in repo.rglob("*.md"):
        if ".git" in path.parts or path.stat().st_size > 500_000:
            continue
        for match in re.finditer(r"[A-Za-z]:\\+Users\\+([A-Za-z0-9._-]{3,})", path.read_text(errors="replace")):
            _add(found, match.group(1))
    # anything that the owner adds by hand
    if extra_file and pathlib.Path(extra_file).is_file():
        for line in pathlib.Path(extra_file).read_text().splitlines():
            if line.strip() and not line.startswith("#"):
                found.add(line.strip())
    return {t for t in found if len(t) >= 4}


def write(terms, path):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    path.write_text("# Private. Outside both repositories. Never commit this file.\n" + "\n".join(sorted(terms, key=str.lower)) + "\n")
    os.chmod(path, 0o600)
    return len(terms)
