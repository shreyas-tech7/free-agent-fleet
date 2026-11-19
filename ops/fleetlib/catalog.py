"""The tool catalog: config/catalog.yaml. Outside tools, skill sources, models, and lists, each with a rating.

The owner writes the file. Agents read it and cannot change it. The research agent reads it before the weekly
scouting. The security agent reads it at the start of the install pipeline. A rating is not an approval.
Each install still needs the install gate and the owner's approval. See D-076.
"""
import pathlib
import re

import yaml

from ops.fleetlib import paths

RATINGS = ("adopt", "trial", "watch", "reject")
STATUSES = ("installed", "vendored", "planned", "none")
KINDS = ("runtime", "router", "scanner", "skill", "skill-source", "mcp", "tool", "service", "model", "catalog", "site")
AGENTS = ("orchestrator", "research", "build", "marketing", "feedback", "security", "improver")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,60}$")
REQUIRED = ("name", "url", "kind", "license", "rating", "status", "used_by", "why", "next")


class CatalogError(ValueError):
    pass


def default_path():
    return paths.repo_root() / "config" / "catalog.yaml"


def problems_of(data):
    """Every problem of a loaded catalog, as short texts. An empty list means that the catalog is valid."""
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list) or not data["entries"]:
        return ["the catalog needs a list named entries"]
    out, seen_names, seen_urls = [], set(), set()
    for i, entry in enumerate(data["entries"]):
        if not isinstance(entry, dict):
            out.append("entry %d is not a mapping" % i)
            continue
        name = str(entry.get("name", "entry %d" % i))
        for key in REQUIRED:
            if key not in entry:
                out.append("%s: %s is missing" % (name, key))
        if not NAME_RE.match(str(entry.get("name", ""))):
            out.append("%s: the name must be lower case letters, digits, dots, and dashes" % name)
        if not str(entry.get("url", "")).startswith("https://"):
            out.append("%s: the url must start with https://" % name)
        if entry.get("rating") not in RATINGS:
            out.append("%s: the rating must be one of %s" % (name, ", ".join(RATINGS)))
        if entry.get("status") not in STATUSES:
            out.append("%s: the status must be one of %s" % (name, ", ".join(STATUSES)))
        if entry.get("kind") not in KINDS:
            out.append("%s: the kind must be one of %s" % (name, ", ".join(KINDS)))
        used = entry.get("used_by")
        if not isinstance(used, list) or any(a not in AGENTS for a in used):
            out.append("%s: used_by must be a list of agent names" % name)
        for key in ("why", "next", "license"):
            if not isinstance(entry.get(key), str) or not entry.get(key, "").strip():
                out.append("%s: %s must be a short text" % (name, key))
        if entry.get("status") in ("installed", "vendored") and not str(entry.get("pin", "")).strip():
            out.append("%s: an installed or vendored entry needs a pin" % name)
        if entry.get("rating") == "reject" and entry.get("status") != "none":
            out.append("%s: a rejected entry must have the status none" % name)
        if entry.get("rating") == "reject" and isinstance(used, list) and used:
            out.append("%s: no agent may use a rejected entry" % name)
        if name in seen_names:
            out.append("%s: the name is used twice" % name)
        url = str(entry.get("url", "")).rstrip("/").lower()
        if url in seen_urls:
            out.append("%s: the url is listed twice" % name)
        seen_names.add(name)
        seen_urls.add(url)
    return out


def load(path=None):
    """The entries of the catalog. Raises CatalogError when the file is missing or not valid."""
    path = pathlib.Path(path or default_path())
    try:
        data = yaml.safe_load(path.read_text())
    except OSError:
        raise CatalogError("the catalog file %s is missing" % path) from None
    except yaml.YAMLError:
        raise CatalogError("the catalog file is not valid YAML") from None
    problems = problems_of(data)
    if problems:
        raise CatalogError("the catalog is not valid: " + "; ".join(problems[:5]))
    return data["entries"]


def _key(url):
    """owner/repo of a GitHub address in lower case, or the address without its scheme and last slash."""
    text = str(url).strip().lower()
    text = re.sub(r"^https?://", "", text).rstrip("/")
    text = re.sub(r"\.git$", "", text)
    m = re.match(r"(?:www\.)?github\.com/([^/]+/[^/#?]+)", text)
    return m.group(1) if m else text


def find(entries, name_or_url):
    """The entry with this name or this source address, or None. A GitHub address matches by owner and repository."""
    want = str(name_or_url or "").strip()
    if not want:
        return None
    for entry in entries:
        if entry["name"] == want.lower():
            return entry
    key = _key(want)
    for entry in entries:
        if _key(entry["url"]) == key:
            return entry
    return None


def may_request(entry):
    """True when an agent may file an install request for this entry. A rejected entry never. An unknown source may."""
    return entry is None or entry["rating"] != "reject"
