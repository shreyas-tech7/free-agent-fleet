import json
import os

import pytest

from ops.control import n8nwatch
from ops.fleetlib import config, httpc, paths


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def reply(ok=True):
    def get(url):
        if not ok:
            raise httpc.TransportError("down")
        return httpc.Response(200, {}, json.dumps({"status": "ok"}).encode())
    return get


@pytest.fixture
def watch(fleet_env, tmp_path):
    clock, sent = Clock(), []
    state = {"ok": True}
    w = n8nwatch.N8nWatch(config.defaults(), sent.append, get=lambda url: reply(state["ok"])(url), clock=clock, heartbeat=tmp_path / "hb.json")
    return w, clock, sent, state


def test_three_misses_in_a_row_make_one_alert_and_the_return_makes_one_note(watch):
    w, clock, sent, state = watch
    state["ok"] = False
    assert w.check_once() == [] and w.check_once() == []
    assert w.check_once() == ["n8n is down. It failed 3 checks in a row. Run: fleetctl logs n8n"]
    assert w.check_once() == [] and w.check_once() == []                  # one alert only, not one every minute
    state["ok"] = True
    assert w.check_once() == ["n8n is back."]
    assert w.check_once() == []
    assert len(sent) == 2


def test_a_single_miss_is_not_an_alert(watch):
    w, _c, sent, state = watch
    state["ok"] = False
    w.check_once()
    state["ok"] = True
    assert w.check_once() == [] and sent == []


def test_a_new_outage_after_a_return_alerts_again(watch):
    w, _c, sent, state = watch
    state["ok"] = False
    for _ in range(3):
        w.check_once()
    state["ok"] = True
    w.check_once()
    state["ok"] = False
    for _ in range(3):
        w.check_once()
    assert [m for m in sent if m.startswith("n8n is down")].__len__() == 2


def test_a_stale_heartbeat_with_a_healthy_n8n_is_one_alert(watch, tmp_path):
    w, clock, sent, state = watch
    (paths.ops_state_dir() / "n8n").mkdir(parents=True, exist_ok=True)
    (paths.ops_state_dir() / "n8n" / "bootstrap-state.json").write_text(json.dumps({"workflows": {"health-watch": "x"}}))
    hb = tmp_path / "hb.json"
    hb.write_text("{}")
    os.utime(hb, (clock.t - 3000, clock.t - 3000))                         # 50 minutes old
    w.check_once()                                                          # first healthy round starts the clock
    assert w.check_once() == []                                             # not yet: n8n has been healthy for under 20 minutes
    clock.t += 1300
    os.utime(hb, (clock.t - 3000, clock.t - 3000))
    out = w.check_once()
    assert len(out) == 1 and "health-watch workflow has not run for 50 minutes" in out[0]
    assert w.check_once() == []                                             # once only
    os.utime(hb, (clock.t - 30, clock.t - 30))
    assert w.check_once() == []                                             # a fresh heartbeat clears the alert state
    assert w.stale_alerted is False


def test_no_heartbeat_alert_before_the_workflows_were_published(watch):
    w, clock, sent, state = watch
    w.check_once()
    clock.t += 5000
    assert w.check_once() == []                                             # bootstrap has not run, so no heartbeat is expected


def test_n8n_switched_off_in_the_config_is_not_watched(fleet_env, tmp_path):
    cfg = config.defaults()
    cfg["n8n"]["enabled"] = False
    sent = []
    w = n8nwatch.N8nWatch(cfg, sent.append, get=reply(False), heartbeat=tmp_path / "hb.json")
    for _ in range(5):
        assert w.check_once() == []


def test_the_loop_sends_the_alerts_that_n8n_could_not_send(watch, tmp_path, monkeypatch):
    import threading
    from ops.control import unsent
    unsent._SEEN.clear()
    w, clock, sent, state = watch
    folder = tmp_path / "unsent"
    folder.mkdir()
    (folder / "alert-9-owner.json").write_text(json.dumps({"text": "n8n workflow failed: health-watch at Check. 401 (run 9)"}))
    monkeypatch.setattr(unsent, "unsent_dir", lambda: folder)
    stop = threading.Event()
    stop.wait = lambda interval=None: stop.set() or True          # run one round, then stop
    w.loop(stop, interval=0)
    assert sent == ["Late alert. n8n workflow failed: health-watch at Check. 401 (run 9)"]
    assert (folder / "sent" / "alert-9-owner.json").exists()


def test_a_failure_while_sending_unsent_alerts_does_not_stop_the_loop(watch, monkeypatch):
    import threading
    from ops.control import unsent
    w, clock, sent, state = watch

    def boom(notify):
        raise RuntimeError("disk")
    monkeypatch.setattr(unsent, "deliver", boom)
    rounds = []
    stop = threading.Event()
    stop.wait = lambda interval=None: (rounds.append(1), stop.set())[1] or True
    w.loop(stop, interval=0)
    assert rounds == [1]


def test_a_broken_channel_does_not_stop_the_watch(fleet_env, tmp_path):
    def broken(text):
        raise OSError("telegram is down")
    w = n8nwatch.N8nWatch(config.defaults(), broken, get=reply(False), heartbeat=tmp_path / "hb.json")
    for _ in range(3):
        out = w.check_once()
    assert out and w.down_alerted is True
