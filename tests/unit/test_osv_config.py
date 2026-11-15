"""The reviewed OSV ignores. Each one needs a reason and a near expiry, so a finding cannot hide for long. See D-051."""
import datetime
import pathlib
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONFIG = ROOT / "vendor" / "osv-scanner.toml"
LATEST = datetime.datetime(2027, 1, 31, tzinfo=datetime.timezone.utc)


def entries():
    return tomllib.loads(CONFIG.read_text())["IgnoredVulns"]


def test_every_ignore_has_an_id_a_real_reason_and_an_expiry_that_is_not_far_away():
    seen = set()
    for e in entries():
        assert e["id"].startswith(("GHSA-", "PYSEC-", "CVE-")) and e["id"] not in seen
        seen.add(e["id"])
        assert len(e["reason"]) >= 40 and e["reason"].rstrip().endswith(".")
        assert isinstance(e["ignoreUntil"], datetime.datetime) and e["ignoreUntil"] <= LATEST, e["id"]


def test_the_list_stays_short():
    assert 1 <= len(entries()) <= 12


def test_the_reasons_name_the_sealed_gate_or_the_tool_that_uses_the_package():
    text = CONFIG.read_text()
    assert "gate container" in text and "no network" in text.lower()


def test_the_config_sits_beside_the_lock_files_that_it_covers():
    assert (ROOT / "vendor" / "gate-mcpscan-requirements.txt").is_file() and (ROOT / "vendor" / "gate-semgrep-requirements.txt").is_file()
