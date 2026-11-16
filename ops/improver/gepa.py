"""The weekly GEPA run for the improver. Plain code. It uses only the standard library.

GEPA (reflective prompt evolution) comes from NousResearch/hermes-agent-self-evolution. It makes better versions of one skill.
This job runs as a script job of the improver profile, on the host, once a week. No agent runs it.

It skips with a clear message when it cannot run safely:
  - the fleet is paused
  - a free daily model limit is near (budget.json says slowdown)
  - the tool is not installed
  - the router has no working model yet (WAITING_FOR_KEYS)

Otherwise it evolves one skill, chosen by the week number, with a small iteration count. The output goes to
projects/_improver/gepa/<date>/ in the working clone. The improver reads it and may open a branch. The merger gates decide.
"""
import datetime
import json
import os
import pathlib
import subprocess
import sys
import urllib.error
import urllib.request

REPO = os.environ.get("FLEET_REPO", "/opt/agent-fleet/repo")
sys.path.insert(0, REPO)
from ops.fleetlib import paths, redact  # noqa: E402

VENV_PYTHON = "/opt/agent-fleet/venvs/evolve/bin/python"
ROUTER = os.environ.get("FLEET_ROUTER_URL", "http://127.0.0.1:20128/v1")
ITERATIONS = 5
TIMEOUT_SECONDS = 3000


def read_env_value(path, name):
    try:
        for line in pathlib.Path(path).read_text().splitlines():
            line = line.strip()
            if line.startswith(name + "="):
                value = line.split("=", 1)[1].strip()
                return value[1:-1] if len(value) > 1 and value[0] == value[-1] and value[0] in "'\"" else value
    except OSError:
        pass
    return ""


def skip_reason(state_dir, venv_python, key):
    state_dir = pathlib.Path(state_dir)
    if (state_dir / "PAUSED").exists():
        return "skipped: the fleet is paused"
    try:
        if json.loads((state_dir / "budget.json").read_text()).get("slowdown"):
            return "skipped: a free daily model limit is near"
    except (OSError, ValueError):
        pass
    if not pathlib.Path(venv_python).exists():
        return "skipped: the GEPA tool is not installed. Run install/43-evolve.sh"
    if not key:
        return "skipped: no router key for this profile yet (WAITING_FOR_KEYS)"
    return None


def pick_skill(names, week):
    names = sorted(n for n in names if not n.startswith("_"))
    return names[week % len(names)] if names else None


def model_available(key, urlopen=urllib.request.urlopen):
    body = json.dumps({"model": "fleet-worker", "messages": [{"role": "user", "content": "ok"}], "max_tokens": 1}).encode()
    req = urllib.request.Request(ROUTER + "/chat/completions", data=body, method="POST",
                                 headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=90) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def command(skill, repo=REPO):
    return [VENV_PYTHON, "-m", "evolution.skills.evolve_skill", "--skill", skill, "--iterations", str(ITERATIONS), "--eval-source", "synthetic",
            "--optimizer-model", "openai/fleet-smart", "--eval-model", "openai/fleet-worker", "--hermes-repo", repo]


def run(now=None, urlopen=urllib.request.urlopen, runner=subprocess.run):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    profile_env = pathlib.Path(os.environ.get("HERMES_HOME", str(pathlib.Path.home() / ".hermes"))) / ".env"
    key = read_env_value(profile_env, "ROUTER_KEY_HERMES")
    reason = skip_reason(paths.state_dir(), VENV_PYTHON, key)
    if reason:
        return reason, 0
    if not model_available(key, urlopen):
        return "skipped: the router has no working model yet (WAITING_FOR_KEYS)", 0
    skills_dir = pathlib.Path(REPO) / "skills"
    skill = pick_skill([p.name for p in skills_dir.iterdir() if (p / "SKILL.md").is_file()], now.isocalendar()[1])
    if skill is None:
        return "skipped: there are no skills", 0
    out = paths.work_clone() / "projects" / "_improver" / "gepa" / now.strftime("%Y-%m-%d")
    out.mkdir(parents=True, exist_ok=True)
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(out), "LANG": "C.UTF-8", "OPENAI_API_BASE": ROUTER, "OPENAI_API_KEY": key,
           "LITELLM_LOCAL_MODEL_COST_MAP": "True", "DSPY_CACHEDIR": str(out / ".dspy-cache")}
    try:
        proc = runner(command(skill), cwd=str(out), env=env, capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return "stopped: the run for %s took longer than %d seconds" % (skill, TIMEOUT_SECONDS), 1
    tail = " ".join((proc.stdout or "").strip().splitlines()[-1:])[:160]
    if proc.returncode != 0:
        # The first weekly run failed with only "exit 1" in the cron note. Keep the last line of the error, with secrets hidden. See D-071.
        why = redact.redact_text(" ".join((proc.stderr or "").strip().splitlines()[-1:])[:200])
        return "failed for %s (exit %d)%s" % (skill, proc.returncode, ": " + why if why else ""), 1
    return "evolved %s. Output in projects/_improver/gepa/%s. %s" % (skill, now.strftime("%Y-%m-%d"), tail), 0


def main(argv=None):
    message, code = run()
    print(message)
    return code


if __name__ == "__main__":
    sys.exit(main())
