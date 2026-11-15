"""fleetctl doctor: one line for each component.

States:
  OK                 works
  WAITING_FOR_KEYS   works, and waits for a key or an owner step (Prompt 2 or `fleetctl setup`)
  DEGRADED           works with less safety or less speed than it should
  FAIL               broken

The exit code is 0 only when every component is OK or WAITING_FOR_KEYS.
No check prints a secret value.
"""
import json
import os
import pathlib
import stat
import time

import yaml

from core.policy import rule_of_two
from ops.fleetctl import system
from ops.fleetlib import config, jsonio, paths, pause, secrets
from ops.fleetlib.status import DEGRADED, FAIL, OK, SKIPPED, WAITING, Component

AGENTS = rule_of_two.REQUIRED_AGENTS
HERMES_HOME_FALLBACK = "/home/fleet/.hermes"


class Ctx:
    def __init__(self, cfg, run=system.run, http=system.http_json, now=time.time):
        self.cfg = cfg
        self.run = run
        self.http = http
        self.now = now

    def hermes_home(self):
        return paths.hermes_home()

    def live(self):
        return paths.live_repo() if paths.live_repo().is_dir() else paths.repo_root()


def _mode(path):
    try:
        st = os.lstat(str(path))
    except OSError:
        return None
    return st.st_uid, stat.S_IMODE(st.st_mode)


def _owner_name(uid):
    import pwd
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


# ------------------------------------------------------------------ base system
def check_config(ctx):
    problems = config.validate(ctx.cfg)
    if problems:
        return [Component("config", FAIL, "; ".join(problems[:3]))]
    return [Component("config", OK, "tier %s, time zone %s" % (config.get(ctx.cfg, "capacity.tier"), config.get(ctx.cfg, "owner.timezone")))]


def check_os(ctx):
    text = ""
    try:
        text = pathlib.Path("/etc/os-release").read_text()
    except OSError:
        pass
    name = next((l.split("=", 1)[1].strip('"') for l in text.splitlines() if l.startswith("PRETTY_NAME=")), "unknown")
    systemd = pathlib.Path("/run/systemd/system").is_dir()
    return [Component("os", OK if systemd else FAIL, name if systemd else "systemd is not the init system")]


def check_users(ctx):
    problems = []
    for user in ("fleet", "fleetops"):
        rc, groups = ctx.run(["id", "-nG", user])
        if rc != 0:
            problems.append("user %s is missing" % user)
            continue
        bad = {"sudo", "docker", "wheel", "adm"} & set(groups.split())
        if bad:
            problems.append("%s is in group %s" % (user, ",".join(sorted(bad))))
        rc, status = ctx.run(["passwd", "-S", user])
        if rc == 0 and len(status.split()) > 1 and status.split()[1] not in ("L", "LK"):
            problems.append("%s has a usable password" % user)
    if problems:
        return [Component("users", FAIL, "; ".join(problems))]
    return [Component("users", OK, "fleet and fleetops: no sudo, no docker group, password login locked")]


def check_sudoers(ctx):
    rc, out = ctx.run(["sudo", "-n", "-l", "-U", "fleetops"])
    rules = [l for l in out.splitlines() if l.strip().startswith("(")] if rc == 0 else []
    info = _mode("/etc/sudoers.d/agent-fleet-control")
    if info is None:
        return [Component("sudoers", FAIL, "/etc/sudoers.d/agent-fleet-control is missing")]
    if info != (0, 0o440):
        return [Component("sudoers", FAIL, "the sudoers file must be root and mode 440")]
    if len(rules) != 1:
        return [Component("sudoers", FAIL, "fleetops must have exactly one sudo rule. Found %d." % len(rules))]
    note = ""
    if pathlib.Path("/etc/sudoers.d/99-fleet-bootstrap").exists():
        note = ". The bootstrap sudo file for the owner still exists. Prompt 2 removes it."
    return [Component("sudoers", OK, "one rule for fleetops (kill switch only)" + note)]


def check_docker(ctx):
    out = []
    bad = []
    for user in ("fleet", "fleetops"):
        rc, text = ctx.run(["docker", "info", "--format", "{{.ServerVersion}} options={{.SecurityOptions}}"], user=user, timeout=40)
        if rc != 0 or "name=rootless" not in text:
            bad.append("%s: rootless Docker does not answer" % user)
        else:
            out.append("%s ok" % user)
    enabled = system.unit_enabled("docker.service", ctx.run)
    if enabled == "enabled":
        bad.append("the system Docker daemon is on (decision D-007)")
    if bad:
        return [Component("docker", FAIL, "; ".join(bad))]
    return [Component("docker", OK, "rootless daemons for " + " and ".join(out) + ", system daemon off")]


def check_firewall(ctx):
    rc, out = ctx.run(["ufw", "status", "verbose"])
    if rc != 0:
        return [Component("firewall", FAIL, "cannot read ufw status")]
    if "Status: active" not in out or "deny (incoming)" not in out:
        return [Component("firewall", FAIL, "ufw is not active with default deny incoming")]
    return [Component("firewall", OK, "ufw active, deny incoming, SSH allowed")]


def check_ports(ctx):
    mine = {config.port(ctx.cfg, k) for k in ("router", "guard", "publisher", "deployer", "control", "searxng", "reader", "n8n")}
    mine.add(config.port(ctx.cfg, "n8n") + 1)               # the n8n task runner broker
    exposed = [p for a, p in system.listening_sockets(ctx.run) if p in mine and a not in ("127.0.0.1", "::1")]
    if exposed:
        return [Component("ports", FAIL, "fleet ports reachable from outside: %s" % ", ".join(str(p) for p in sorted(set(exposed))))]
    return [Component("ports", OK, "every fleet port listens on 127.0.0.1 only")]


def check_power(ctx):
    masked = [t for t in ("sleep.target", "suspend.target", "hibernate.target", "hybrid-sleep.target")
              if system.unit_enabled(t, ctx.run) == "masked"]
    if len(masked) < 4:
        return [Component("power", DEGRADED, "sleep targets are not all masked")]
    return [Component("power", OK, "sleep targets masked")]


def check_time(ctx):
    rc, out = ctx.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"])
    if rc == 0 and out.strip() == "yes":
        return [Component("time", OK, "clock is synchronized")]
    return [Component("time", DEGRADED, "the clock is not synchronized")]


def check_updates(ctx):
    rc, out = ctx.run(["systemctl", "is-enabled", "unattended-upgrades"])
    return [Component("os_updates", OK if out.strip() == "enabled" else DEGRADED,
                      "unattended security upgrades on" if out.strip() == "enabled" else "unattended upgrades are off")]


def check_disk(ctx):
    try:
        st = os.statvfs("/var/lib")
    except OSError:
        return [Component("disk", FAIL, "cannot read disk space")]
    free_gb = st.f_bavail * st.f_frsize / 1e9
    if free_gb < 2:
        return [Component("disk", FAIL, "%.1f GB free" % free_gb)]
    if free_gb < 10:
        return [Component("disk", DEGRADED, "%.1f GB free" % free_gb)]
    return [Component("disk", OK, "%.0f GB free" % free_gb)]


def check_tailscale(ctx):
    rc, out = ctx.run(["tailscale", "status"], timeout=20)
    if rc == 127:
        return [Component("tailscale", FAIL, "tailscale is not installed")]
    if "Logged out" in out or "NeedsLogin" in out or "stopped" in out.lower():
        return [Component("tailscale", WAITING, "installed. The owner runs: sudo tailscale up")]
    return [Component("tailscale", OK, "connected")]


# ------------------------------------------------------------------ locked core
def check_locked_core(ctx):
    bad = []
    expect = [("/etc/hermes/config.yaml", 0, 0o644), ("/etc/hermes/.env", 0, 0o644),
              ("/usr/local/bin/fleetctl", 0, 0o755), ("/opt/agent-fleet/core/tests/hidden", 0, 0o700),
              (str(pathlib.Path(ctx.hermes_home()) / "hooks"), 0, 0o755)]
    for path, uid, mode in expect:
        info = _mode(path)
        if info is None and test_mode() and path.endswith("/hooks"):
            continue                                       # the clean-machine test installs no Hermes, so it has no hooks folder. See D-063.
        if info is None:
            bad.append("%s is missing" % path)
        elif info != (uid, mode):
            bad.append("%s is %s %o, expected root %o" % (path, _owner_name(info[0]), info[1], mode))
    live = ctx.live()
    info = _mode(live)
    if info is None or info[0] != 0 or info[1] & 0o022:
        bad.append("the live repository must be root and not group or world writable")
    for rel in ("core/RULES.md", "core/killswitch/killswitch.py", "ops/merger/runner.py"):
        info = _mode(live / rel)
        if info is None or info[0] != 0 or info[1] & 0o022:
            bad.append("%s is not locked" % rel)
    if bad:
        return [Component("locked_core", FAIL, "; ".join(bad[:3]))]
    return [Component("locked_core", OK, "rules, hooks, managed config, hidden tests and live code are root only")]


REQUIRED_MANAGED = [("approvals.mode", "smart"), ("approvals.cron_mode", "deny"), ("terminal.backend", "docker"),
                    ("security.redact_secrets", True), ("updates.check", False)]


def check_managed_config(ctx):
    try:
        doc = yaml.safe_load(pathlib.Path("/etc/hermes/config.yaml").read_text()) or {}
    except (OSError, yaml.YAMLError):
        return [Component("hermes_managed", FAIL, "/etc/hermes/config.yaml is missing or invalid")]
    bad = [k for k, v in REQUIRED_MANAGED if config.get(doc, k, "missing") != v]
    if not config.get(doc, "approvals.deny", []):
        bad.append("approvals.deny")
    want = config.get(ctx.cfg, "router.base_url")
    if config.get(doc, "model.base_url") != want:
        bad.append("model.base_url")
    if bad:
        return [Component("hermes_managed", FAIL, "wrong or missing: %s" % ", ".join(bad))]
    return [Component("hermes_managed", OK, "pins approvals, sandbox, redaction, deny list and router URL")]


def check_hermes(ctx):
    rc, out = ctx.run(["hermes", "--version"], user="fleet", timeout=60)
    want = config.get(ctx.cfg, "hermes.version_tag", "")
    if rc != 0:
        return [Component("hermes", FAIL, "hermes does not run as the user fleet")]
    if want and want.lstrip("v") not in out:
        return [Component("hermes", FAIL, "version differs from the pin %s" % want)]
    return [Component("hermes", OK, out.splitlines()[0][:60])]


def check_profiles(ctx):
    live = ctx.live()
    home = pathlib.Path(ctx.hermes_home()) / "profiles"
    bad = []
    names = [p.name for p in sorted((live / "agents").glob("*")) if p.is_dir()] if (live / "agents").is_dir() else []
    for need in AGENTS:
        if need not in names:
            bad.append("agents/%s is missing" % need)
    for name in names:
        prof = home / name
        if not prof.is_dir():
            bad.append("profile %s is not created" % name)
            continue
        soul_repo = live / "agents" / name / "SOUL.md"
        try:
            if (prof / "SOUL.md").read_text() != soul_repo.read_text():
                bad.append("profile %s has an old SOUL.md" % name)
        except OSError:
            bad.append("profile %s has no SOUL.md" % name)
    if bad:
        return [Component("profiles", FAIL, "; ".join(bad[:3]))]
    return [Component("profiles", OK, "%d agent profiles match the repository" % len(names))]


def _hermes_config(ctx):
    try:
        return yaml.safe_load((pathlib.Path(ctx.hermes_home()) / "config.yaml").read_text()) or {}
    except (OSError, yaml.YAMLError):
        return None


def check_kanban(ctx):
    doc = _hermes_config(ctx)
    if doc is None:
        return [Component("kanban", FAIL, "cannot read the Hermes config")]
    bad = []
    if config.get(doc, "kanban.dispatch_in_gateway") is not True:
        bad.append("dispatch_in_gateway is not true")
    if config.get(doc, "kanban.orchestrator_profile") != "orchestrator":
        bad.append("orchestrator_profile is not orchestrator")
    if config.get(doc, "kanban.auto_decompose") is not False:
        bad.append("auto_decompose is not false")
    profiles = config.get(doc, "kanban.dispatch_profiles", "absent")
    paused = pause.paused() is not None
    if paused and profiles != []:
        bad.append("the fleet is paused but the dispatcher is not stopped")
    if not paused and profiles != "absent":
        bad.append("the fleet runs but dispatch_profiles is set")
    if bad:
        return [Component("kanban", FAIL, "; ".join(bad))]
    return [Component("kanban", OK, "dispatcher in the gateway, orchestrator decomposes, %s" % ("stopped by the pause" if paused else "running"))]


def check_pause(ctx):
    info = pause.paused()
    if info is None:
        return [Component("fleet_state", OK, "running")]
    reason = info.get("reason", "")
    if reason == "waiting_for_keys":
        return [Component("fleet_state", WAITING, "paused until keys exist. After setup: fleetctl resume")]
    return [Component("fleet_state", OK, "paused (%s) since %s" % (reason or "owner", info.get("since", "?")))]


# ------------------------------------------------------------------ secrets
def check_secrets(ctx):
    out, bad = [], []
    groups = [g for g in secrets.registry()["groups"] if g != "internal"]
    seen_files = {}
    for entry in secrets.all_secrets():
        seen_files.setdefault(str(secrets.resolve_path(entry["file"])), entry["owner"])
    for path, owner in seen_files.items():
        info = _mode(path)
        if info is None:
            continue
        if info[1] != 0o600 or _owner_name(info[0]) != owner:
            bad.append("%s must be %s mode 600" % (path, owner))
    for group in groups:
        rows = secrets.status_rows(group)
        have = sum(1 for r in rows if r["set"])
        state = OK if have else WAITING
        out.append(Component("keys_%s" % group, state, "%d of %d set" % (have, len(rows))))
    internal = secrets.status_rows("internal")
    missing = [r["name"] for r in internal if not r["set"]]
    if missing and test_mode():                            # the router, n8n, and SearXNG keys come from steps that need Docker. See D-063.
        out.append(Component("keys_internal", SKIPPED, "container test mode: %d keys come from steps that need Docker" % len(missing)))
    else:
        out.append(Component("keys_internal", FAIL if missing else OK,
                             ("missing: %s" % ", ".join(missing[:3])) if missing else "%d made by the installer" % len(internal)))
    out.append(Component("secret_files", FAIL if bad else OK, "; ".join(bad[:2]) if bad else "owners and mode 600 are right"))
    return out


# ------------------------------------------------------------------ services
def _service(ctx, name):
    unit, port_key, path = system.SERVICES[name]
    state = system.unit_state(unit, ctx.run)
    if state != "active":
        return None, "unit is %s" % state
    if port_key and path:
        status, data = ctx.http("http://127.0.0.1:%d%s" % (config.port(ctx.cfg, port_key), path))
        if status != 200:
            return None, "unit is active but %s answers with status %s" % (path, status or "none")
        return data, ""
    return {}, ""


def check_router(ctx):
    data, why = _service(ctx, "router")
    if why:
        return [Component("router", FAIL, why)]
    has_key = any(r["set"] for r in secrets.status_rows("router"))
    if not has_key and not config.get(ctx.cfg, "features.mock_llm", False):
        return [Component("router", WAITING, "up on 127.0.0.1:%d. No provider key yet." % config.port(ctx.cfg, "router"))]
    approved = config.get(ctx.cfg, "router.approved_providers", [])
    if not approved and not config.get(ctx.cfg, "features.mock_llm", False):
        return [Component("router", WAITING, "up. A key exists. No provider is approved yet: fleetctl router approve NAME")]
    return [Component("router", OK, "up, approved providers: %s" % (", ".join(approved) or "mock model"))]


def check_guard(ctx):
    data, why = _service(ctx, "guard")
    if why:
        return [Component("guard", FAIL, why)]
    if (data or {}).get("status") == "OK":
        return [Component("guard", OK, "mode %s" % data.get("mode", "?"))]
    return [Component("guard", DEGRADED, "rules only. The model did not load. Mode %s." % (data or {}).get("mode", "?"))]


def check_searxng(ctx):
    _data, why = _service(ctx, "searxng")
    return [Component("searxng", FAIL, why) if why else Component("searxng", OK, "up on 127.0.0.1:%d" % config.port(ctx.cfg, "searxng"))]


def check_reader(ctx):
    data, why = _service(ctx, "reader")
    return [Component("reader", FAIL, why) if why else Component("reader", OK, "converter %s" % (data or {}).get("converter", "?"))]


def _n8n_pinned_version(ctx):
    """The version that this machine is set to run: the compose .env, which `fleetctl n8n upgrade` moves."""
    try:
        from ops.n8n import prepare
        value = prepare.read_env_file(prepare.run_dir() / ".env").get("N8N_VERSION")
        return value or prepare.read_pins(ctx.live())["N8N_VERSION"]
    except Exception:                                  # no files in a test tree. The version check is skipped.
        return None


def _n8n_running_version(ctx):
    """The version label of the image of the running container."""
    rc, out = ctx.run(["docker", "inspect", "agent-fleet-n8n", "--format", '{{index .Config.Labels "org.opencontainers.image.version"}}'],
                      timeout=30, user="fleetops")
    out = out.strip()
    return out if rc == 0 and out[:1].isdigit() else None


def check_n8n(ctx):
    """n8n runs, answers /healthz, and runs the pinned version. See D-036."""
    if not config.get(ctx.cfg, "n8n.enabled", True):
        return [Component("n8n", OK, "off in the config")]
    state = system.unit_state(system.SERVICES["n8n"][0], ctx.run)
    if state != "active":
        return [Component("n8n", FAIL, "unit is %s" % state)]
    port = config.port(ctx.cfg, "n8n")
    status, data = ctx.http("http://127.0.0.1:%d/healthz" % port)
    if status != 200 or (data or {}).get("status") != "ok":
        return [Component("n8n", FAIL, "unit is active but /healthz answers with status %s" % (status or "none"))]
    running, pinned = _n8n_running_version(ctx), _n8n_pinned_version(ctx)
    if pinned and running and running != pinned:
        return [Component("n8n", FAIL, "runs %s but %s is pinned. Run: fleetctl n8n upgrade %s" % (running, pinned, pinned))]
    return [Component("n8n", OK, "n8n %s healthy on 127.0.0.1:%d" % (running or pinned or "?", port))]


def check_n8n_workflows(ctx):
    """Every fleet workflow exists and is published. It needs the API key that `fleetctl n8n bootstrap` makes."""
    if not config.get(ctx.cfg, "n8n.enabled", True):
        return []
    key = secrets.value_of("N8N_API_KEY")
    if not key:
        return [Component("n8n_workflows", FAIL, "no API key yet. Run: fleetctl n8n bootstrap")]
    from ops.n8n import client as nclient
    from ops.n8n import smoke
    _name, ok, detail = smoke.check_workflows(nclient.Client("http://127.0.0.1:%d" % config.port(ctx.cfg, "n8n")), key)
    return [Component("n8n_workflows", OK if ok else FAIL, detail)]


def check_events(ctx):
    """The event relay. Events wait in the spool while n8n is down. A long queue means that n8n or the token is wrong."""
    status = jsonio.read_json(paths.ops_state_dir() / "events" / "status.json", {}) or {}
    if not status:
        return [Component("events", OK, "the relay has not sent an event yet")]
    depth, oldest = int(status.get("depth", 0)), int(status.get("oldest_seconds", 0))
    if depth > 50 or oldest > 900:
        return [Component("events", DEGRADED, "%d events wait. The oldest has waited %d minutes. Is n8n up?" % (depth, oldest // 60))]
    return [Component("events", OK, "%d waiting, %s sent, %s dropped" % (depth, status.get("sent", 0), status.get("dropped", 0)))]


def check_unsent_alerts(ctx):
    """Alerts that n8n could not send wait as files. The control bot sends them within a minute. A file that waits longer means a problem."""
    if not config.get(ctx.cfg, "n8n.enabled", True):
        return []
    from ops.control import unsent
    count, oldest = unsent.waiting(now=ctx.now())
    if count and oldest > 600:
        return [Component("unsent_alerts", DEGRADED, "alerts wait for the control bot: %d. The oldest waited %d minutes. Run: fleetctl logs control" % (count, oldest // 60))]
    return [Component("unsent_alerts", OK, "%d waiting" % count)]


def check_n8n_backup(ctx):
    if not config.get(ctx.cfg, "n8n.enabled", True):
        return []
    try:
        from ops.n8n import backup
        found = backup.list_backups()
    except Exception:
        found = []
    if not found:
        return [Component("n8n_backup", OK, "none yet. The nightly timer runs at 02:20")]
    age = ctx.now() - found[-1].stat().st_mtime
    if age > 36 * 3600:
        return [Component("n8n_backup", DEGRADED, "the newest backup is %d hours old" % (age // 3600))]
    return [Component("n8n_backup", OK, "newest %s, %d kept" % (found[-1].name, len(found)))]


def check_publisher(ctx):
    data, why = _service(ctx, "publisher")
    if why:
        return [Component("publisher", FAIL, why)]
    status, body = ctx.http("http://127.0.0.1:%d/platforms" % config.port(ctx.cfg, "publisher"))
    plats = (body or {}).get("platforms", {}) if status == 200 else {}
    ready = [n for n, p in plats.items() if p.get("enabled") and p.get("ready")]
    mode = (data or {}).get("mode", "?")
    if not ready:
        return [Component("publisher", WAITING, "%s mode. No platform has keys yet." % mode)]
    return [Component("publisher", OK, "%s mode, ready: %s" % (mode, ", ".join(sorted(ready))))]


def check_deployer(ctx):
    data, why = _service(ctx, "deployer")
    if why:
        return [Component("deployer", FAIL, why)]
    mode = (data or {}).get("mode", "?")
    if not secrets.is_set("CLOUDFLARE_API_TOKEN"):
        return [Component("deployer", WAITING, "%s mode. No Cloudflare token yet." % mode)]
    return [Component("deployer", OK, "%s mode, free plan only" % mode)]


def check_control(ctx):
    data, why = _service(ctx, "control")
    if why:
        return [Component("control_bot", FAIL, why)]
    channels = (data or {}).get("channels", [])
    if not channels:
        return [Component("control_bot", WAITING, "up and idle. No Telegram or Discord bot is set. Run: fleetctl secret wizard --group control")]
    return [Component("control_bot", OK, "listening on %s" % ", ".join(channels))]


def check_action_token(ctx):
    """n8n calls the control bot with the action token. The running bot must accept it.

    The bot holds only the hash of the token, in a file that is bind-mounted into its sandbox. A bot that started before the
    hash existed still sees the old file and answers 401. The fix is a restart. See D-053.
    """
    if not config.get(ctx.cfg, "n8n.enabled", True):
        return []
    token = secrets.value_of("FLEET_ACTION_TOKEN")
    if not token:
        return [Component("n8n_action_token", WAITING, "no action token yet. Run: sudo fleetctl n8n prepare")]
    status, _body = ctx.http("http://127.0.0.1:%d/v1/status" % config.port(ctx.cfg, "control"), headers={"Authorization": "Bearer " + token})
    if status == 200:
        return [Component("n8n_action_token", OK, "the control bot accepts the action token of n8n")]
    if status == 401:
        return [Component("n8n_action_token", FAIL, "the control bot refuses the action token, so n8n cannot reach it. Run: sudo systemctl restart agent-fleet-control")]
    if status == 0:
        return [Component("n8n_action_token", DEGRADED, "the control bot did not answer, so the action token was not tested")]
    return [Component("n8n_action_token", DEGRADED, "the control bot answered with status %s" % status)]


def check_slack(ctx):
    """The Slack adapter. It idles until the Fleet HQ tokens exist. See D-052."""
    hq = [secrets.is_set("SLACK_HQ_BOT_TOKEN"), secrets.is_set("SLACK_HQ_APP_TOKEN")]
    hermes = [secrets.is_set("SLACK_HERMES_BOT_TOKEN"), secrets.is_set("SLACK_HERMES_APP_TOKEN")]
    out = []
    if not all(hq):
        out.append(Component("slack", WAITING, "the Fleet HQ tokens are not set. Make the app from slack/fleet-hq.manifest.json. See docs/SLACK.md"))
    else:
        state = system.unit_state(system.SERVICES["slack"][0], ctx.run)
        channels = config.get(ctx.cfg, "slack.channels", {}) or {}
        if state != "active":
            out.append(Component("slack", FAIL, "unit is %s" % state))
        elif not channels:
            out.append(Component("slack", DEGRADED, "the tokens are set, but no channel is known. Run: fleetctl slack bootstrap"))
        elif config.get(ctx.cfg, "slack.sink_mode", True):
            out.append(Component("slack", DEGRADED, "the app runs, but messages still go to the sink. Run: fleetctl config set slack.sink_mode false"))
        else:
            out.append(Component("slack", OK, "Fleet HQ is connected, %d channels" % len(channels)))
    out.append(Component("slack_hermes", OK if all(hermes) else WAITING, "the Hermes tokens are set" if all(hermes) else "the Hermes tokens are not set. See docs/SLACK.md"))
    return out


def check_board(ctx):
    """The board exporter. It writes a snapshot every 45 seconds for the Slack #board mirror."""
    state = system.unit_state(system.SERVICES["board"][0], ctx.run)
    if state != "active":
        return [Component("board_export", FAIL, "unit is %s" % state)]
    from ops.board import export
    try:
        age = ctx.now() - export.snapshot_file().stat().st_mtime
    except OSError:
        return [Component("board_export", DEGRADED, "the snapshot does not exist yet")]
    if age > 300:
        return [Component("board_export", DEGRADED, "the snapshot is %d minutes old" % (age // 60))]
    return [Component("board_export", OK, "snapshot %d seconds old" % age)]


def check_gateway(ctx):
    state = system.unit_state(system.SERVICES["gateway"][0], ctx.run)
    if state != "active":
        return [Component("gateway", FAIL, "unit is %s" % state)]
    path = pathlib.Path(ctx.hermes_home()) / "gateway_state.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return [Component("gateway", DEGRADED, "unit is active. No state file yet.")]
    if data.get("gateway_state") not in ("running", "starting"):
        return [Component("gateway", DEGRADED, "state %s" % data.get("gateway_state"))]
    return [Component("gateway", OK, "Hermes gateway runs the Kanban dispatcher and the cron ticker")]


# ------------------------------------------------------------------ timers and data
def check_timers(ctx):
    out = []
    for name, unit in system.TIMERS.items():
        state = system.unit_state(unit, ctx.run)
        out.append(Component("timer_%s" % name, OK if state == "active" else FAIL, "waiting" if state == "active" else "timer is %s" % state))
    return out


def check_budget(ctx):
    path = paths.budget_file()
    try:
        age = ctx.now() - path.stat().st_mtime
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return [Component("budget", DEGRADED, "budget.json is missing. The quota timer has not run yet.")]
    if age > 3600:
        return [Component("budget", DEGRADED, "budget.json is %d minutes old" % (age // 60))]
    return [Component("budget", OK, "slowing down: %s" % ", ".join(data.get("hot", [])) if data.get("slowdown") else "no free limit is near")]


def check_backup(ctx):
    folder = paths.ops_state_dir() / "backups"
    try:
        newest = max((p for p in folder.glob("agent-fleet-*.tar.gz")), key=lambda p: p.stat().st_mtime)
    except (OSError, ValueError):
        return [Component("backup", DEGRADED, "no backup archive yet")]
    age_h = (ctx.now() - newest.stat().st_mtime) / 3600
    if age_h > 48:
        return [Component("backup", DEGRADED, "newest backup is %d hours old" % age_h)]
    return [Component("backup", OK, "newest backup %s (%d hours old)" % (newest.name, age_h))]


GEPA_PYTHON = "/opt/agent-fleet/venvs/evolve/bin/python"
GEPA_SOURCE = "/opt/agent-fleet/vendor/hermes-agent-self-evolution"


def check_gepa(ctx):
    installed = pathlib.Path(GEPA_PYTHON).exists() and pathlib.Path(GEPA_SOURCE).is_dir()
    if not installed:
        return [Component("gepa", FAIL, "the GEPA runner is not installed. Run: sudo bash install/43-evolve.sh")]
    has_key = any(r["set"] for r in secrets.status_rows("router")) or config.get(ctx.cfg, "features.mock_llm", False)
    if not has_key:
        return [Component("gepa", WAITING, "installed. The weekly run starts after the first model key.")]
    return [Component("gepa", OK, "installed. It runs on Sunday at 02:00 when a model works and no limit is near.")]


def check_merger(ctx):
    bad = []
    if not pathlib.Path("/opt/agent-fleet/gate-image").is_file():
        bad.append("gate image is not built")
    info = _mode(paths.hidden_tests_dir())
    if info is None:
        bad.append("hidden tests are missing")
    if bad:
        return [Component("merger", FAIL, "; ".join(bad))]
    state = system.unit_state("agent-fleet-merger.timer", ctx.run)
    return [Component("merger", OK if state == "active" else FAIL, "gates ready, hidden tests present, timer %s" % state)]


CHECKS = [check_config, check_os, check_users, check_sudoers, check_docker, check_firewall, check_ports, check_power,
          check_time, check_updates, check_disk, check_tailscale, check_locked_core, check_managed_config, check_hermes,
          check_profiles, check_kanban, check_pause, check_secrets, check_router, check_guard, check_searxng,
          check_reader, check_n8n, check_n8n_workflows, check_events, check_unsent_alerts, check_n8n_backup, check_publisher, check_deployer, check_control, check_action_token, check_slack, check_board, check_gateway, check_timers, check_budget,
          check_backup, check_gepa, check_merger]


# In the clean-machine test, install.sh runs in a container with no systemd and no Docker. See D-051.
# The marker file says so. The checks below still run. Every other check reports SKIPPED.
CONTAINER_KEEP = {"check_config", "check_users", "check_locked_core", "check_managed_config", "check_pause", "check_secrets"}


def test_mode():
    return pathlib.Path(os.environ.get("FLEET_TEST_MODE_FILE", "/etc/agent-fleet/test-mode")).exists()


def run_all(ctx, only=None):
    results = []
    container = test_mode()
    for fn in CHECKS:
        if only and only not in fn.__name__:
            continue
        if container and fn.__name__ not in CONTAINER_KEEP:
            results.append(Component(fn.__name__.replace("check_", ""), SKIPPED, "container test mode: needs systemd, Docker, or a running service"))
            continue
        try:
            results += fn(ctx)
        except Exception as exc:                      # one broken check must not hide the others
            results.append(Component(fn.__name__.replace("check_", ""), FAIL, "the check crashed: %s" % type(exc).__name__))
    return results


def exit_code(results):
    return 0 if all(c.status in (OK, WAITING, SKIPPED) for c in results) else 1


def render(results):
    lines = [c.line() for c in results]
    counts = {s: sum(1 for c in results if c.status == s) for s in (OK, WAITING, DEGRADED, FAIL)}
    lines.append("")
    skipped = sum(1 for c in results if c.status == SKIPPED)
    lines.append("Result: %d OK, %d WAITING_FOR_KEYS, %d DEGRADED, %d FAIL%s" % (counts[OK], counts[WAITING], counts[DEGRADED], counts[FAIL],
                                                                              ", %d SKIPPED (container test mode)" % skipped if skipped else ""))
    return "\n".join(lines)


def cmd_doctor(args, console, runner=system.run):
    cfg = config.load()
    ctx = Ctx(cfg, run=runner)
    results = run_all(ctx, getattr(args, "only", None))
    if getattr(args, "json", False):
        console.say(json.dumps([{"name": c.name, "status": c.status, "detail": c.detail} for c in results], indent=2))
    else:
        console.say(render(results))
    code = exit_code(results)
    if not getattr(args, "json", False):
        console.say("Exit code %d." % code)
    return code
