"""Alerts that n8n could not send. The workflow leaves a file. The control bot sends the text later. See D-041."""
import json
import os
import time

import pytest

from ops.control import unsent


def put(folder, name, body, age=0):
    path = folder / name
    path.write_text(body if isinstance(body, str) else json.dumps(body))
    if age:
        moment = time.time() - age
        os.utime(path, (moment, moment))
    return path


@pytest.fixture
def folder(tmp_path):
    unsent._SEEN.clear()
    d = tmp_path / "unsent"
    d.mkdir()
    return d


def test_a_waiting_alert_is_sent_once_and_the_file_moves_to_sent(folder):
    put(folder, "alert-77-slack.json", {"file": "alert-77-slack.json", "channel": "slack", "text": "n8n workflow failed: health-watch at Check. boom (run 77)"})
    sent = []
    assert unsent.deliver(sent.append, folder) == 1
    assert sent == ["Late alert. n8n workflow failed: health-watch at Check. boom (run 77)"]
    assert not (folder / "alert-77-slack.json").exists() and (folder / "sent" / "alert-77-slack.json").exists()
    assert unsent.deliver(sent.append, folder) == 0 and len(sent) == 1


def test_a_file_that_holds_a_list_of_one_alert_is_read_too(folder):
    put(folder, "alert-1-owner.json", [{"text": "one"}])
    sent = []
    assert unsent.deliver(sent.append, folder) == 1 and sent == ["Late alert. one"]


def test_the_text_is_one_line_and_has_a_length_limit(folder):
    put(folder, "alert-1-owner.json", {"text": "line one\n\n   line two " + "x" * 2000})
    sent = []
    unsent.deliver(sent.append, folder)
    assert "\n" not in sent[0] and len(sent[0]) <= len("Late alert. ") + unsent.MAX_TEXT


def test_a_chat_that_got_nothing_leaves_the_file_and_stops_the_round(folder):
    put(folder, "alert-1-slack.json", {"text": "a"})
    put(folder, "alert-2-slack.json", {"text": "b"})
    calls = []

    def nobody(text):
        calls.append(text)
        return 0
    assert unsent.deliver(nobody, folder) == 0 and len(calls) == 1
    assert sorted(p.name for p in folder.glob("alert-*.json")) == ["alert-1-slack.json", "alert-2-slack.json"]


def test_a_notifier_that_raises_leaves_the_file(folder):
    put(folder, "alert-1-slack.json", {"text": "a"})

    def broken(text):
        raise RuntimeError("down")
    assert unsent.deliver(broken, folder) == 0
    assert (folder / "alert-1-slack.json").exists()


def test_a_count_of_one_chat_counts_as_sent(folder):
    put(folder, "alert-1-slack.json", {"text": "a"})
    assert unsent.deliver(lambda text: 1, folder) == 1


@pytest.mark.parametrize("body", ["not json", "[]", "{}", '{"text": ""}', '{"text": 5}', '"just a string"', '{"text": "' + "x" * 9000 + '"}'])
def test_a_file_that_is_not_a_valid_alert_moves_to_bad_and_is_never_sent(folder, body):
    put(folder, "alert-1-slack.json", body)
    sent = []
    assert unsent.deliver(sent.append, folder) == 0 and sent == []
    assert (folder / "bad" / "alert-1-slack.json").exists() and not (folder / "alert-1-slack.json").exists()


def test_files_that_are_not_alerts_are_left_alone(folder, tmp_path):
    put(folder, "notes.txt", "hello")
    put(folder, "alert-1-slack.txt", "hello")
    outside = tmp_path / "secret.json"
    outside.write_text(json.dumps({"text": "from outside"}))
    os.symlink(outside, folder / "alert-9-slack.json")
    (folder / "alert-8-slack.json").mkdir()
    sent = []
    assert unsent.deliver(sent.append, folder) == 0 and sent == []
    assert unsent.waiting(folder) == (0, 0)


def test_one_round_sends_at_most_the_limit(folder):
    for i in range(7):
        put(folder, "alert-%d-slack.json" % i, {"text": "n%d" % i})
    sent = []
    assert unsent.deliver(sent.append, folder, limit=3) == 3 and len(sent) == 3
    assert unsent.deliver(sent.append, folder, limit=3) == 3 and unsent.deliver(sent.append, folder, limit=3) == 1 and len(sent) == 7


def test_a_file_that_cannot_be_moved_is_not_sent_again_in_the_same_run(folder, monkeypatch):
    put(folder, "alert-1-slack.json", {"text": "a"})

    def cannot_move(path, sub):
        raise OSError("read-only")
    monkeypatch.setattr(unsent, "_move", cannot_move)
    sent = []
    unsent.deliver(sent.append, folder)
    unsent.deliver(sent.append, folder)
    assert sent == ["Late alert. a"]


def test_waiting_counts_the_files_and_the_age_of_the_oldest(folder):
    assert unsent.waiting(folder) == (0, 0)
    put(folder, "alert-1-slack.json", {"text": "a"}, age=900)
    put(folder, "alert-2-owner.json", {"text": "b"}, age=60)
    count, oldest = unsent.waiting(folder)
    assert count == 2 and 890 <= oldest <= 910
    assert unsent.waiting(folder / "missing") == (0, 0)


def test_the_default_folder_is_inside_the_n8n_files_folder(fleet_env):
    assert str(unsent.unsent_dir()).endswith("/n8n/files/unsent")
