"""Approvals. The owner decides. Four kinds: spec, release, install, and public_sync.

One approval is one JSON file in /var/lib/agent-fleet-ops/approvals. The control bot writes it.
The owner decides with a Slack button, or with /approve ID and /reject ID in Telegram or Discord.
An approval never turns off a dry-run switch. It says only that the owner agrees with one named item.
A pending approval expires after 72 hours. See D-039.
"""
import json
import pathlib
import re
import secrets as pysecrets
import time

from ops.fleetlib import jsonio, log, paths

LOG = log.get("approvals")
KINDS = ("spec", "release", "install", "public_sync")
TTL_SECONDS = 72 * 3600
REF_RE = re.compile(r"^[A-Za-z0-9._:/@#+-]{1,80}$")
ID_RE = re.compile(r"^a[0-9a-f]{5}$")
MAX_SUMMARY = 400


STATUS_KEEP_SECONDS = 14 * 86400


class ApprovalError(ValueError):
    pass


def status_file():
    return paths.state_dir() / "status" / "approvals.json"


def folder():
    return paths.ops_state_dir() / "approvals"


def _path(approval_id, base=None):
    if not ID_RE.match(str(approval_id)):
        raise ApprovalError("the approval id must look like a1b2c3")
    return pathlib.Path(base or folder()) / (approval_id + ".json")


def _expire(record, now):
    if record["status"] == "pending" and now - record["created"] > TTL_SECONDS:
        record = dict(record, status="expired")
    return record


def create(kind, ref, summary, requester="", now=None, base=None):
    if kind not in KINDS:
        raise ApprovalError("the kind must be one of: %s" % ", ".join(KINDS))
    if not isinstance(ref, str) or not REF_RE.match(ref):
        raise ApprovalError("the reference is not valid")
    summary = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(summary or "")).strip()[:MAX_SUMMARY]
    if not summary:
        raise ApprovalError("a short summary is required")
    now = int(now if now is not None else time.time())
    # One pending approval for one item. A second request returns the first.
    for record in pending(now=now, base=base):
        if record["kind"] == kind and record["ref"] == ref:
            return record
    base_dir = pathlib.Path(base or folder())
    base_dir.mkdir(parents=True, exist_ok=True)
    approval_id = "a" + pysecrets.token_hex(3)[:5]
    while (base_dir / (approval_id + ".json")).exists():
        approval_id = "a" + pysecrets.token_hex(3)[:5]
    record = {"id": approval_id, "kind": kind, "ref": ref, "summary": summary, "requester": str(requester)[:32],
              "status": "pending", "created": now, "decided_by": "", "decided_at": 0}
    jsonio.write_json(base_dir / (approval_id + ".json"), record, mode=0o640)
    if base is None:
        publish_status(now=now)
    return record


def get(approval_id, now=None, base=None):
    try:
        record = json.loads(_path(approval_id, base).read_text())
    except FileNotFoundError:
        raise ApprovalError("no approval with that id") from None
    except ValueError:
        raise ApprovalError("the approval file is damaged") from None
    return _expire(record, int(now if now is not None else time.time()))


def decide(approval_id, approve, by, now=None, base=None):
    """Approve or reject a pending approval. A decision is final."""
    now = int(now if now is not None else time.time())
    record = get(approval_id, now=now, base=base)
    if record["status"] != "pending":
        raise ApprovalError("that approval is already %s" % record["status"])
    record.update(status="approved" if approve else "rejected", decided_by=str(by)[:32], decided_at=now)
    jsonio.write_json(_path(approval_id, base), record, mode=0o640)
    if base is None:
        publish_status(now=now)
    return record


def all_records(now=None, base=None):
    now = int(now if now is not None else time.time())
    out = []
    for p in sorted(pathlib.Path(base or folder()).glob("a*.json")):
        try:
            out.append(_expire(json.loads(p.read_text()), now))
        except (OSError, ValueError, KeyError):
            continue
    return out


def pending(now=None, base=None):
    return [r for r in all_records(now, base) if r["status"] == "pending"]


def is_approved(kind, ref, now=None, base=None):
    """True when the owner approved this item. Gates call this before a spec build, a deploy, an install, or a public sync."""
    return any(r["kind"] == kind and r["ref"] == ref and r["status"] == "approved" for r in all_records(now, base))


def status_summary(now=None, base=None):
    """What an agent may learn: the kind, the reference, the status, and when it began. No summary text and no names."""
    now = int(now if now is not None else time.time())
    out = {}
    for r in all_records(now, base):
        if now - r["created"] > STATUS_KEEP_SECONDS:
            continue
        key = "%s:%s" % (r["kind"], r["ref"])
        if key not in out or r["created"] >= out[key]["created"]:
            out[key] = {"status": r["status"], "created": r["created"]}
    return out


def publish_status(now=None, base=None, out=None):
    """Write the status file in the fleet-readable status folder. The agents read it with the approval_status tool. See D-050."""
    now = int(now if now is not None else time.time())
    path = pathlib.Path(out or status_file())
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        jsonio.write_json(path, {"updated": now, "approvals": status_summary(now, base)}, mode=0o644)
        return True
    except OSError as exc:
        LOG.warn("approval_status_not_written", error=type(exc).__name__)
        return False
