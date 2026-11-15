"""The hold on owner messages. A selftest uses it so that a test never wakes the owner. See D-043."""
import json
import os
import time

import pytest

from ops.control import hold, main as control_main, slackout
from ops.fleetlib import config


@pytest.fixture
def files(tmp_path):
    return tmp_path / "control" / "hold-until", tmp_path / "outbox" / "owner-hold.jsonl"


def test_there_is_no_hold_without_the_file(files):
    path, _ = files
    assert hold.seconds_left(path=path) == 0 and hold.active(path=path) is False


def test_a_hold_lasts_the_seconds_that_were_set_and_then_ends(files):
    path, _ = files
    now = time.time()
    assert hold.start(120, now=now, path=path) == 120
    assert hold.active(now=now + 60, path=path) and 55 <= hold.seconds_left(now=now + 60, path=path) <= 60
    assert not hold.active(now=now + 121, path=path)
    assert oct(path.stat().st_mode & 0o777) == "0o644"


def test_a_hold_is_never_longer_than_fifteen_minutes(files):
    path, _ = files
    now = time.time()
    assert hold.start(10 * 3600, now=now, path=path) == hold.MAX_SECONDS
    assert hold.start(0, now=now, path=path) == 1


def test_a_forged_far_future_value_is_capped_by_the_age_of_the_file(files):
    path, _ = files
    path.parent.mkdir(parents=True)
    path.write_text("%.0f\n" % (time.time() + 10 * 3600))
    assert hold.active(path=path)                              # the file is new, so the cap has not passed
    old = time.time() - hold.MAX_SECONDS - 5
    os.utime(path, (old, old))
    assert not hold.active(path=path)


@pytest.mark.parametrize("text", ["", "soon", "nan", "\x00", "12 34"])
def test_a_file_with_a_wrong_value_is_no_hold(files, text):
    path, _ = files
    path.parent.mkdir(parents=True)
    path.write_text(text)
    assert not hold.active(path=path)


def test_stop_ends_the_hold_and_is_safe_to_repeat(files):
    path, _ = files
    hold.start(60, path=path)
    hold.stop(path=path)
    hold.stop(path=path)
    assert not hold.active(path=path)


def test_a_held_message_goes_to_the_sink_with_its_kind(files):
    _, sink = files
    hold.record("chat", "selftest: notify_owner works", sink=sink, now=1_800_000_000)
    row = json.loads(sink.read_text())
    assert row == {"kind": "chat", "text": "selftest: notify_owner works", "ts": 1_800_000_000}
    assert oct(sink.stat().st_mode & 0o777) == "0o640"


def test_the_sink_rotates_when_it_is_large(files):
    _, sink = files
    sink.parent.mkdir(parents=True)
    sink.write_text("x" * (hold.MAX_SINK_BYTES + 1))
    hold.record("chat", "new", sink=sink)
    assert sink.with_name(sink.name + ".1").exists() and json.loads(sink.read_text())["text"] == "new"


class Boom:
    name = "telegram"

    def notify_owner(self, text):
        raise AssertionError("a held message must not reach a chat")


def test_notify_all_sends_nothing_to_the_chats_during_a_hold(fleet_env, monkeypatch, tmp_path):
    held = []
    monkeypatch.setattr(hold, "active", lambda *a, **k: True)
    monkeypatch.setattr(hold, "record", lambda kind, text, **k: held.append((kind, text)))
    assert control_main.notify_all([Boom()], "selftest message") == 0
    assert held == [("chat", "selftest message")]


def test_notify_all_sends_again_when_the_hold_ends(fleet_env, monkeypatch):
    class Chat:
        name = "telegram"
        sent = []

        def notify_owner(self, text):
            self.sent.append(text)
    monkeypatch.setattr(hold, "active", lambda *a, **k: False)
    chat = Chat()
    assert control_main.notify_all([chat], "real message") == 1 and chat.sent == ["real message"]


def test_slack_posts_go_to_the_hold_sink_and_not_to_the_slack_sink_during_a_hold(fleet_env, monkeypatch, tmp_path):
    held = []
    monkeypatch.setattr(hold, "active", lambda *a, **k: True)
    monkeypatch.setattr(hold, "record", lambda kind, text, **k: held.append((kind, text)))
    out = slackout.SlackOut(config.defaults(), sink_file=tmp_path / "slack.jsonl")
    assert out.post("alerts", "selftest alert", severity="warn") == {"ok": True, "mode": "hold"}
    assert held == [("slack:alerts", "selftest alert")] and not (tmp_path / "slack.jsonl").exists()


def test_a_slack_post_with_no_hold_still_uses_the_slack_sink(fleet_env, monkeypatch, tmp_path):
    monkeypatch.setattr(hold, "active", lambda *a, **k: False)
    out = slackout.SlackOut(config.defaults(), sink_file=tmp_path / "slack.jsonl")
    assert out.post("alerts", "real alert")["mode"] == "sink" and (tmp_path / "slack.jsonl").exists()
