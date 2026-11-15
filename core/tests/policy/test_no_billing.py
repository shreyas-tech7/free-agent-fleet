"""Policy: no config holds billing settings or paid-only endpoints (rule R1)."""
import json
import pathlib

import yaml

from core.policy import denylists as dl

ROOT = pathlib.Path(__file__).resolve().parents[3]


def config_files():
    files = []
    for pattern in ("config/**/*.yaml", "config/**/*.yml", "config/**/*.json", "agents/*/agent.yaml"):
        files += [p for p in ROOT.glob(pattern) if p.is_file()]
    return sorted(set(files))


def walk(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield path + "/" + str(k), k, v
            yield from walk(v, path + "/" + str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk(v, "%s[%d]" % (path, i))


def load(path):
    text = path.read_text()
    return json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)


def test_there_are_config_files_to_check():
    assert len(config_files()) >= 4


def test_no_billing_keys():
    bad = []
    for f in config_files():
        for where, key, _ in walk(load(f) or {}):
            if any(b in str(key).lower() for b in dl.BILLING_KEYS):
                bad.append("%s %s" % (f.relative_to(ROOT), where))
    assert not bad, bad


def test_no_paid_only_endpoints():
    bad = []
    for f in config_files():
        text = f.read_text().lower()
        for host in dl.PAID_ENDPOINTS:
            if host in text:
                bad.append("%s -> %s" % (f.relative_to(ROOT), host))
    assert not bad, bad


def test_no_paid_plan_names():
    bad = []
    for f in config_files():
        for where, key, value in walk(load(f) or {}):
            if str(key).lower() in ("plan", "tier") and str(value).lower() in dl.PAID_PLAN_WORDS:
                bad.append("%s %s=%s" % (f.relative_to(ROOT), where, value))
    assert not bad, bad


def test_deployer_plan_is_free_in_defaults():
    cfg = yaml.safe_load((ROOT / "config" / "fleet.example.yaml").read_text())
    assert cfg["deployer"]["plan"] == "free"
