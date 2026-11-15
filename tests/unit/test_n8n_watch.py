import datetime
import json

import pytest

from ops.fleetlib import httpc
from ops.n8n import watch

NOW = 1_800_000_000.0


def iso(days_ago):
    return datetime.datetime.fromtimestamp(NOW - days_ago * 86400, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


RELEASES = [
    {"tag_name": "n8n@2.47.7", "published_at": iso(1.8), "prerelease": False},
    {"tag_name": "n8n@2.47.6", "published_at": iso(1.9), "prerelease": False},
    {"tag_name": "n8n@2.47.5", "published_at": iso(2.9), "prerelease": False},
    {"tag_name": "n8n@2.47.4", "published_at": iso(3.9), "prerelease": False},
    {"tag_name": "n8n@2.46.7", "published_at": iso(6.0), "prerelease": False},
    {"tag_name": "n8n@3.0.0-beta.1", "published_at": iso(5.0), "prerelease": True},
    {"tag_name": "n8n@3.0.1", "published_at": iso(1.0), "prerelease": False},
    {"tag_name": "n8n@1.123.90", "published_at": iso(1.0), "prerelease": False},
]


def adv(ghsa, severity, patched):
    return {"ghsa_id": ghsa, "severity": severity, "published_at": iso(1), "vulnerabilities": [{"patched_versions": patched}]}


ADVISORIES = [
    adv("GHSA-high-new", "high", ">= 2.47.6; >= 2.48.3; >= 3.2.2"),             # the real shape: fixed in my line at 2.47.6
    adv("GHSA-crit-old", "critical", ">= 1.123.43; >= 2.20.7; >= 2.22.1"),         # long fixed
    adv("GHSA-only-1x-3x", "high", ">= 1.123.89; >= 3.0.1"),                        # no 2.x fix to move to
    adv("GHSA-medium", "medium", ">= 2.50.0"),                                       # not high enough to matter
    adv("GHSA-later-line", "critical", ">= 2.50.1"),                                 # fixed only in a later line
    adv("GHSA-fixed-before", "high", ">= 2.45.5; >= 2.46.1"),                       # fixed before my version
]


def get(releases=RELEASES, advisories=ADVISORIES, ok=True):
    def request(method, url, **kw):
        if not ok:
            raise httpc.TransportError("down")
        body = releases if "/releases" in url else advisories
        return httpc.Response(200, {}, json.dumps(body).encode())
    return request


def test_only_stable_2x_releases_count_and_the_newest_is_first():
    ranked = watch.stable_2x(RELEASES, NOW)
    assert [r[1] for r in ranked] == ["2.47.7", "2.47.6", "2.47.5", "2.47.4", "2.46.7"]


def test_the_open_advisories_for_a_running_version():
    rows = watch.open_advisories(ADVISORIES, "2.47.4")
    assert {r[1]: (r[2], r[3]) for r in rows} == {"GHSA-high-new": ("high", "2.47.6"), "GHSA-later-line": ("critical", "2.50.1")}


def test_a_version_with_the_fix_has_no_open_advisory_for_it():
    assert [r[1] for r in watch.open_advisories(ADVISORIES, "2.47.6")] == ["GHSA-later-line"]
    assert watch.open_advisories(ADVISORIES, "2.50.1") == []


def test_the_release_check_names_the_newest_release_that_is_three_days_old():
    emitted = []
    out = watch.check_releases("2.46.7", NOW, get(), emit=lambda *a, **k: emitted.append((a, k)))
    assert out["status"] == "ok" and out["ready"] == "2.47.4" and out["waiting"] == ["2.47.7", "2.47.6", "2.47.5"]
    text = emitted[0][0][1]
    assert "fleetctl n8n upgrade 2.47.4" in text and "3 newer releases wait for age" in text
    assert emitted[0][0][0] == "n8n.update_check"


def test_a_critical_advisory_makes_the_event_a_warning():
    emitted = []
    watch.check_releases("2.47.4", NOW, get(), emit=lambda *a, **k: emitted.append((a, k)))
    assert emitted[0][1]["severity"] == "warn" and "GHSA-later-line" in emitted[0][0][1]
    quiet = []
    watch.check_releases("2.47.6", NOW, get(advisories=[ADVISORIES[0]]), emit=lambda *a, **k: quiet.append((a, k)))
    assert quiet[0][1]["severity"] == "info"


def test_when_nothing_newer_is_old_enough_it_says_so():
    emitted = []
    watch.check_releases("2.47.4", NOW, get(releases=RELEASES[:3], advisories=[]), emit=lambda *a, **k: emitted.append((a, k)))
    assert "No newer release is 3 days old yet" in emitted[0][0][1] and "fleetctl n8n upgrade" not in emitted[0][0][1]


def test_an_unreadable_github_is_a_warning_event_and_a_failed_status():
    emitted = []
    assert watch.check_releases("2.47.4", NOW, get(ok=False), emit=lambda *a, **k: emitted.append((a, k)))["status"] == "unreadable"
    assert emitted[0][0][0] == "n8n.update_check_failed" and emitted[0][1]["severity"] == "warn"


class Migration:
    def __init__(self, report=None, status=200):
        self.report, self.status, self.calls = report, status, []

    def login(self, email, password):
        self.calls.append("login")

    def call(self, method, path, body=None, **kw):
        self.calls.append((method, path))
        return self.status, ({"data": {"report": self.report}} if self.report is not None else None)


REPORT = {"currentVersion": "2.47.4", "targetVersion": "v2", "workflowResults": [{}, {}],
          "instanceResults": [{"instanceIssues": [{"level": "info"}, {"level": "warning"}]}, {"instanceIssues": [{"level": "info"}]}]}


def test_the_migration_report_is_summed_up_as_one_event():
    emitted = []
    out = watch.check_migration(Migration(REPORT), "o@example.org", "pw", emit=lambda *a, **k: emitted.append((a, k)))
    assert out == {"status": "ok", "target": "v2", "instance_rules": 2, "workflow_findings": 2}
    text = emitted[0][0][1]
    assert "info 2, warning 1" in text and "2 workflow findings" in text and "still reports against v2" in text
    assert emitted[0][0][0] == "n8n.migration_report"


def test_a_v3_report_has_no_wait_note_and_it_refreshes_first():
    emitted = []
    m = Migration(dict(REPORT, targetVersion="v3"))
    watch.check_migration(m, "o@example.org", "pw", emit=lambda *a, **k: emitted.append((a, k)))
    assert "still reports" not in emitted[0][0][1] and m.calls[:2] == ["login", ("POST", "/rest/breaking-changes/report/refresh")]


def test_an_unreadable_migration_report_is_a_warning():
    emitted = []
    assert watch.check_migration(Migration(None, 500), "o@example.org", "pw", emit=lambda *a, **k: emitted.append((a, k)))["status"] == "unreadable"
    assert emitted[0][1]["severity"] == "warn"
