"""Prepare n8n for its first start. Run as root by install/56-n8n.sh and by `fleetctl n8n prepare`.

What it does, in order:
 1. Make the folders for the user fleetops: data, files, backups, exports.
 2. Make the secrets that the installer owns: the encryption key, the owner password, and the local tokens.
 3. Make the bcrypt hash of the owner password. It uses the bcrypt module inside the pinned n8n image.
    The password goes in on standard input. It is never in an argument.
 4. Write the compose .env. It holds no secret.
 5. Pull the pinned image into the rootless Docker of fleetops.

It is safe to run twice. It never prints a secret value and never prints the owner email.
"""
import hashlib
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys

from ops.fleetctl import system
from ops.fleetlib import autosecrets, config, jsonio, log, paths, secrets

LOG = log.get("n8n-prepare")
PLACEHOLDER_EMAIL = "owner@example.com"
N8N_SECRETS_FILE = "/etc/agent-fleet/secrets/n8n.env"
# The secrets that this module makes. FLEET_ACTION_TOKEN is a derived token: the control bot keeps only its hash.
AUTO_SECRETS = ["N8N_ENCRYPTION_KEY", "N8N_OWNER_PASSWORD", "FLEET_EVENT_TOKEN", "FLEET_ACTION_TOKEN", "N8N_TOOLS_TOKEN"]
KEEP_FROM_OLD_ENV = ("N8N_EDITOR_BASE_URL", "WEBHOOK_URL", "N8N_PROXY_HOPS")
HASH_RE = re.compile(r"^\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}$")

# bcrypt inside the n8n image. The password is read from standard input. The script prints the hash, then a check word.
BCRYPT_JS = ('const fs=require("fs");const pw=fs.readFileSync(0,"utf8").trim();'
             'let b;try{b=require("bcryptjs");}catch(e){b=require("bcrypt");}'
             'const h=b.hashSync(pw,10);console.log(h);console.log(b.compareSync(pw,h)?"verify-ok":"verify-FAILED");')


class PrepareError(Exception):
    pass


def run_dir():
    return paths.ops_state_dir() / "n8n"


def read_pins(root=None):
    path = pathlib.Path(root or paths.repo_root()) / "vendor" / "pins.env"
    out = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip().strip('"')
    for need in ("N8N_IMAGE", "N8N_VERSION"):
        if need not in out:
            raise PrepareError("vendor/pins.env has no %s" % need)
    return out


def _clean_name(text, default):
    text = re.sub(r"[^\w .'-]", "", text or "").replace("'", "").strip()
    return text[:30] or default


def split_name(full):
    parts = (full or "").split(None, 1)
    return _clean_name(parts[0] if parts else "", "Owner"), _clean_name(parts[1] if len(parts) > 1 else "", "Account")


def git_value(key, runner=system.run):
    """`git config --global KEY` of the person who ran sudo. Empty when there is none."""
    user = os.environ.get("SUDO_USER") or ""
    if not user or user == "root":
        return ""
    rc, out = runner(["git", "config", "--global", "--get", key], timeout=15, user=user)
    return out.strip() if rc == 0 else ""


def owner_identity(cfg, git=git_value):
    """(email, first name, last name). Order: git config of the owner, the live config, a placeholder."""
    email = git("user.email")
    name = git("user.name")
    if not email or "@" not in email:
        email = config.get(cfg, "owner.email", "") or ""
    if not email or "@" not in email:
        email = PLACEHOLDER_EMAIL
    if not name:
        name = config.get(cfg, "owner.name", "") or ""
        if name == "Your Name":
            name = ""
    first, last = split_name(name)
    return email.strip(), first, last


def swap_limit(memory):
    """1g gives 1536m. A value in m gets half as much again."""
    m = re.fullmatch(r"([0-9]+)([mg])", memory)
    if not m:
        raise PrepareError("n8n.memory must look like 1g or 768m")
    mb = int(m.group(1)) * (1024 if m.group(2) == "g" else 1)
    return "%dm" % (mb * 3 // 2)


def _quote(value):
    value = str(value)
    if re.fullmatch(r"[A-Za-z0-9._:/@+=,~-]*", value):
        return value
    return "'" + value.replace("'", "") + "'"


def render_env(pins, identity, timezone, memory, previous=None):
    """The text of the compose .env. It holds no secret."""
    email, first, last = identity
    previous = previous or {}
    values = {
        "N8N_IMAGE": pins["N8N_IMAGE"], "N8N_VERSION": pins["N8N_VERSION"],
        "N8N_MEMORY": memory, "N8N_MEMORY_SWAP": swap_limit(memory), "N8N_TIMEZONE": timezone,
        "N8N_OWNER_EMAIL": email, "N8N_OWNER_FIRST_NAME": first, "N8N_OWNER_LAST_NAME": last,
        "N8N_SECRETS_FILE": N8N_SECRETS_FILE,
    }
    for key in KEEP_FROM_OLD_ENV:                       # set by `fleetctl n8n serve`. A re-run must not lose them.
        if previous.get(key):
            values[key] = previous[key]
    lines = ["# Written by fleetctl n8n prepare. It holds no secret. Do not put one here. Mode 600, never in git."]
    lines += ["%s=%s" % (k, _quote(v)) for k, v in values.items()]
    return "\n".join(lines) + "\n"


def ensure_dirs(base=None, events_base=None, state_base=None):
    """The n8n folders (mode 700, fleetops), the event spools (mode 770), and the card inbox (mode 770).

    The ops spool belongs to fleetops. A service that runs as root writes files there and gives them to fleetops.
    The fleet spool belongs to fleet, in the group fleetops, so the relay can read and delete what the agents' side writes.
    The card inbox belongs to fleetops, in the group fleet. It is in no agent sandbox. See D-038."""
    base = pathlib.Path(base or run_dir())
    events_base = pathlib.Path(events_base or (paths.ops_state_dir() / "events"))
    state_base = pathlib.Path(state_base or paths.state_dir())
    root = os.geteuid() == 0
    made = []
    # n8n does not make the folder of a file that it writes. The workflows write to files/heartbeat, files/ledger, and files/unsent.
    subs = ("", "data", "files", "files/heartbeat", "files/ledger", "files/unsent", "files/unsent/sent", "files/unsent/bad", "backups", "exports")
    wanted = [(base / sub if sub else base, 0o700, "fleetops", "fleetops", sub or ".") for sub in subs]
    wanted += [(events_base, 0o770, "fleetops", "fleetops", "events"), (events_base / "spool", 0o770, "fleetops", "fleetops", "events/spool"),
               (events_base / "bad", 0o770, "fleetops", "fleetops", "events/bad"),
               (state_base / "events", 0o2770, "fleet", "fleetops", "fleet events"), (state_base / "events" / "spool", 0o2770, "fleet", "fleetops", "fleet events/spool"),
               (state_base / "cards-in", 0o2770, "fleetops", "fleet", "cards-in"), (state_base / "cards-in" / "bad", 0o2770, "fleetops", "fleet", "cards-in/bad")]
    for d, mode, owner, group, label in wanted:
        if not d.exists():
            made.append(label)
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, mode)
        if root:
            shutil.chown(d, owner, group)
    return made


def password_marker(password):
    return hashlib.sha256(("n8n-owner:" + password).encode()).hexdigest()


def bcrypt_hash(password, image, runner=system.run):
    """The bcrypt hash of the password, made inside the pinned image. The password is on standard input only."""
    argv = ["docker", "run", "--rm", "-i", "--network", "none", "--cap-drop", "ALL", "--entrypoint", "sh", image,
            "-c", "cd /usr/local/lib/node_modules/n8n && node -e '%s'" % BCRYPT_JS]
    rc, out = runner(argv, timeout=180, user="fleetops", input_text=password)
    lines = [l.strip() for l in out.splitlines() if l.strip()]
    if rc != 0 or len(lines) < 2 or lines[-1] != "verify-ok" or not HASH_RE.match(lines[-2]):
        raise PrepareError("could not make the owner password hash (the image may be missing)")
    return lines[-2]


def ensure_owner_hash(image, runner=system.run):
    """Make or refresh N8N_INSTANCE_OWNER_PASSWORD_HASH. Returns 'kept' or 'made'."""
    password = secrets.value_of("N8N_OWNER_PASSWORD")
    if not password:
        raise PrepareError("N8N_OWNER_PASSWORD is not set")
    admin = secrets.secret_path("N8N_OWNER_PASSWORD")
    mark = secrets.read_values(admin).get("N8N_OWNER_PASSWORD_MARK", "")
    if secrets.is_set("N8N_INSTANCE_OWNER_PASSWORD_HASH") and mark == password_marker(password):
        return "kept"
    secrets.set_value("N8N_INSTANCE_OWNER_PASSWORD_HASH", bcrypt_hash(password, image, runner))
    secrets._update_file(admin, "N8N_OWNER_PASSWORD_MARK", "N8N_OWNER_PASSWORD_MARK=%s" % password_marker(password),
                         "root" if os.geteuid() == 0 else None)
    return "made"


def read_env_file(path):
    return secrets.read_values(path)


def port_in_use(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def image_present(image, runner=system.run):
    rc, _out = runner(["docker", "image", "inspect", image], timeout=60, user="fleetops")
    return rc == 0


def pull_image(image, runner=system.run):
    if image_present(image, runner):
        return "present"
    rc, out = runner(["docker", "pull", "--quiet", image], timeout=1200, user="fleetops")
    if rc != 0:
        raise PrepareError("docker pull failed: %s" % out[-160:])
    return "pulled"


def prepare(cfg=None, runner=system.run, git=git_value, pins=None, unit_active=None):
    """Do every step. Returns a report with no secret in it."""
    cfg = cfg or config.load()
    pins = pins or read_pins()
    report = {"dirs": [], "secrets": [], "repaired": [], "owner_hash": "", "env": "", "image": "", "warnings": []}
    report["dirs"] = ensure_dirs()
    report["secrets"] = autosecrets.ensure(AUTO_SECRETS, repaired=report["repaired"])
    pull = pull_image(pins["N8N_IMAGE"], runner)
    report["image"] = pull
    report["owner_hash"] = ensure_owner_hash(pins["N8N_IMAGE"], runner)
    env_path = run_dir() / ".env"
    previous = read_env_file(env_path)
    memory = config.get(cfg, "n8n.memory", "1g")
    tz = config.get(cfg, "n8n.timezone", "") or config.get(cfg, "owner.timezone", "America/Chicago")
    text = render_env(pins, owner_identity(cfg, git), tz, memory, previous)
    root = os.geteuid() == 0
    jsonio.atomic_write(env_path, text, mode=0o600, owner="fleetops" if root else None, group="fleetops" if root else None)
    report["env"] = "written"
    active = unit_active if unit_active is not None else system.unit_state("agent-fleet-n8n", runner) == "active"
    if not active:
        for port in (5678, 5679):
            if port_in_use(port):
                report["warnings"].append("port %d is in use and the n8n unit is not running" % port)
    return report


def main(argv=None):
    try:
        report = prepare()
    except (PrepareError, secrets.SecretError, config.ConfigError) as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}))
        return 1
    report["status"] = "ok"
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
