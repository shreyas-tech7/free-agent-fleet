import json
import os
import stat

import pytest

from ops.events.relay import HEADER, MAX_SPOOL, Relay
from ops.fleetlib import events, httpc, redact


def resp(status):
    return httpc.Response(status, {}, b"{}")


class Transport:
    """A fake webhook. `answers` is a list of statuses, an exception, or a function."""

    def __init__(self, answer=200):
        self.answer = answer
        self.calls = []

    def __call__(self, method, url, headers=None, json_body=None, timeout=10, **kw):
        self.calls.append({"method": method, "url": url, "headers": dict(headers or {}), "body": json_body})
        a = self.answer(len(self.calls)) if callable(self.answer) else self.answer
        if isinstance(a, Exception):
            raise a
        return resp(a)


@pytest.fixture
def spool(tmp_path):
    return tmp_path / "events" / "spool"


def make(spool, tmp_path, transport=None, now=1_000_000.0, **kw):
    t = transport or Transport()
    clock = {"now": now}
    relay = Relay("http://127.0.0.1:5678/webhook/fleet-event", "tok-abc", spool=spool, bad=tmp_path / "events" / "bad",
                  transport=t, now=lambda: clock["now"], **kw)
    return relay, t, clock


# ------------------------------------------------------------------ emit and build
def test_build_makes_a_small_valid_event():
    e = events.build("backup.ok", "Backup done", source="backup", fields={"files": 12, "mode": "x"}, now=1700000000, event_id="abc")
    assert e == {"v": 1, "id": "abc", "ts": 1700000000, "source": "backup", "type": "backup.ok", "severity": "info", "title": "",
                 "text": "Backup done", "fields": {"files": 12, "mode": "x"}}


@pytest.mark.parametrize("bad", [dict(type_="Backup.OK", text="x"), dict(type_="a..b", text="x"), dict(type_="x", text="y", severity="urgent"),
                                 dict(type_="x", text="y", source="Has Space"), dict(type_="x", text="y", fields={"Bad Key": 1})])
def test_build_refuses_bad_input(bad):
    with pytest.raises(events.EventError):
        events.build(**bad)


def test_text_is_clipped_fields_are_capped_and_control_characters_go():
    e = events.build("x.y", "a" * 5000 + "\x00\x07b", title="t" * 500, fields={"k%d" % i: "v" * 900 for i in range(30)})
    assert len(e["text"]) == events.MAX_TEXT and "\x00" not in e["text"] and "\x07" not in e["text"]
    assert len(e["title"]) == events.MAX_TITLE and len(e["fields"]) == events.MAX_FIELDS
    assert all(len(v) <= events.MAX_VALUE for v in e["fields"].values())


def test_a_registered_secret_never_reaches_the_spool(spool):
    secret = "sk-test-" + "z" * 30
    redact.register_secret(secret)
    eid = events.emit("alert.test", "the key is %s ok" % secret, fields={"k": secret}, spool=spool)
    text = next(spool.glob("*.json")).read_text()
    assert eid and secret not in text


def test_emit_writes_one_file_with_mode_660(spool):
    eid = events.emit("backup.ok", "done", source="backup", spool=spool, now=1700000000)
    files = list(spool.glob("*.json"))
    assert len(files) == 1 and files[0].name == "1700000000000-%s.json" % eid
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o660
    assert json.loads(files[0].read_text())["type"] == "backup.ok"
    assert not list(spool.glob("*.tmp"))


def test_emit_never_raises(tmp_path):
    assert events.emit("Not Valid", "x", spool=tmp_path) is None
    blocked = tmp_path / "file"
    blocked.write_text("x")
    assert events.emit("a.b", "x", spool=blocked / "sub") is None          # the folder cannot be made


# ------------------------------------------------------------------ the relay
def test_a_good_answer_sends_and_deletes_the_file(spool, tmp_path):
    events.emit("backup.ok", "done", spool=spool, now=999_990)
    relay, t, _ = make(spool, tmp_path)
    assert relay.run_once()["sent"] == 1
    assert not list(spool.glob("*.json"))
    call = t.calls[0]
    assert call["method"] == "POST" and call["headers"] == {HEADER: "tok-abc"} and call["body"]["type"] == "backup.ok"
    status = json.loads(relay.status_path.read_text())
    assert status["sent"] == 1 and status["depth"] == 0 and status["last_ok"] == 1_000_000


@pytest.mark.parametrize("answer", [503, 404, 401, 403, httpc.TransportError("down")])
def test_a_failure_keeps_the_file_and_waits_before_the_next_try(spool, tmp_path, answer):
    events.emit("backup.ok", "done", spool=spool, now=999_990)
    relay, t, clock = make(spool, tmp_path, Transport(answer))
    assert relay.run_once()["kept"] == 1 and len(list(spool.glob("*.json"))) == 1
    relay.run_once()
    assert len(t.calls) == 1                                    # still waiting
    clock["now"] += 6
    relay.run_once()
    assert len(t.calls) == 2                                    # tried again after the wait


def test_the_wait_grows_and_stops_at_five_minutes(spool, tmp_path):
    events.emit("backup.ok", "done", spool=spool, now=999_990)
    relay, t, clock = make(spool, tmp_path, Transport(503))
    waits = []
    for _ in range(9):
        relay.run_once()
        name = next(spool.glob("*.json")).name
        waits.append(relay.wait[name][1] - clock["now"])
        clock["now"] = relay.wait[name][1]
    assert waits[:3] == [5, 10, 20] and max(waits) == 300


def test_a_400_answer_quarantines_the_file(spool, tmp_path):
    events.emit("backup.ok", "done", spool=spool, now=999_990)
    relay, t, _ = make(spool, tmp_path, Transport(400))
    assert relay.run_once()["bad"] == 1
    assert not list(spool.glob("*.json")) and len(list((tmp_path / "events" / "bad").glob("*.json"))) == 1


def test_a_corrupt_file_never_blocks_the_others(spool, tmp_path):
    spool.mkdir(parents=True)
    (spool / "0000000000001-broken.json").write_text("{not json")
    events.emit("backup.ok", "done", spool=spool, now=999_990)
    relay, t, _ = make(spool, tmp_path)
    out = relay.run_once()
    assert out["bad"] == 1 and out["sent"] == 1


def test_an_old_event_is_dropped_and_counted(spool, tmp_path):
    events.emit("backup.ok", "old", spool=spool, now=1_000_000 - 25 * 3600)
    relay, t, _ = make(spool, tmp_path)
    assert relay.run_once()["dropped"] == 1 and not t.calls
    assert json.loads(relay.status_path.read_text())["dropped"] == 1


def test_a_file_with_a_forged_type_is_quarantined_not_sent(spool, tmp_path):
    spool.mkdir(parents=True)
    forged = {"v": 1, "id": "f", "ts": 999_990, "source": "x", "type": "Bad Type", "severity": "info", "title": "", "text": "x", "fields": {}}
    (spool / "0000000999990-f.json").write_text(json.dumps(forged))
    relay, t, _ = make(spool, tmp_path)
    assert relay.run_once()["bad"] == 1 and not t.calls


def test_the_spool_is_capped_and_info_events_go_first(spool, tmp_path):
    for i in range(MAX_SPOOL + 5):
        events.emit("x.y", "n%d" % i, severity="critical" if i == 0 else "info", spool=spool, now=999_000 + i)
    relay, t, _ = make(spool, tmp_path, Transport(503))
    relay.run_once()
    left = list(spool.glob("*.json"))
    assert len(left) == MAX_SPOOL
    assert any(json.loads(p.read_text())["severity"] == "critical" for p in left)      # the critical one stays
    assert relay.totals["dropped"] == 5


def test_each_run_sends_at_most_fifty(spool, tmp_path):
    for i in range(60):
        events.emit("x.y", "n%d" % i, spool=spool, now=999_000 + i)
    relay, t, _ = make(spool, tmp_path)
    assert relay.run_once()["sent"] == 50 and len(list(spool.glob("*.json"))) == 10


def test_the_relay_reads_the_spool_of_the_fleet_user_too(spool, tmp_path):
    fleet_spool = tmp_path / "fleet-events" / "spool"
    events.emit("board.card_created", "from the agents side", spool=fleet_spool, now=999_990)
    events.emit("backup.ok", "from the ops side", spool=spool, now=999_991)
    relay, tr, _ = make(spool, tmp_path, extra_spools=[fleet_spool])
    assert relay.run_once()["sent"] == 2
    assert [c["body"]["type"] for c in tr.calls] == ["board.card_created", "backup.ok"]      # oldest first
    assert not list(fleet_spool.glob("*.json")) and not list(spool.glob("*.json"))


def test_a_failed_event_in_the_fleet_spool_waits_there(spool, tmp_path):
    fleet_spool = tmp_path / "fleet-events" / "spool"
    events.emit("board.card_created", "x", spool=fleet_spool, now=999_990)
    relay, tr, _ = make(spool, tmp_path, Transport(503), extra_spools=[fleet_spool])
    assert relay.run_once()["kept"] == 1 and len(list(fleet_spool.glob("*.json"))) == 1
    assert next(iter(relay.wait)) == next(fleet_spool.glob("*.json")).name


def test_the_default_spool_follows_the_user(monkeypatch, tmp_path):
    import pwd
    monkeypatch.setattr(events.paths, "ops_state_dir", lambda: tmp_path / "ops")
    monkeypatch.setattr(events.paths, "state_dir", lambda: tmp_path / "state")

    class P:
        pw_name = "fleet"
    monkeypatch.setattr(pwd, "getpwuid", lambda uid: P)
    assert events.default_spool() == tmp_path / "state" / "events" / "spool"
    P.pw_name = "fleetops"
    assert events.default_spool() == tmp_path / "ops" / "events" / "spool"
