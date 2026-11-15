"""The Slack Web API client. A fake Slack answers. No network and no real token."""
import pytest

from ops.control import slackapi
from ops.fleetlib import httpc
from tests.fake_slack import FakeSlack

TOKEN = "xoxb-" + "placeholder-" + "t" * 20


def api(fake=None, clock=None, sleeps=None, **kw):
    fake = fake or FakeSlack()
    now = clock or (lambda: 0.0)
    return slackapi.SlackApi(TOKEN, request=fake, clock=now, sleep=(sleeps.append if sleeps is not None else (lambda s: None)), **kw), fake


def test_a_call_sends_the_token_only_in_the_header_and_returns_the_answer():
    client, fake = api()
    out = client.call("auth.test")
    assert out == {"ok": True} and fake.headers_seen[0] == {"Authorization": "Bearer " + TOKEN} and TOKEN not in str(fake.calls)


@pytest.mark.parametrize("error", ["invalid_auth", "channel_not_found", "x" * 100])
def test_an_ok_false_answer_is_an_error_with_the_code_and_no_token(error):
    client, _ = api(FakeSlack(fail={"auth.test": error}))
    with pytest.raises(slackapi.SlackApiError) as err:
        client.call("auth.test")
    assert err.value.error == error[:60] and TOKEN not in str(err.value)


def test_a_429_carries_the_wait_time_and_a_down_slack_is_unreachable():
    client, _ = api(FakeSlack(fail={"chat.postMessage": "429"}))
    with pytest.raises(slackapi.SlackApiError) as err:
        client.call("chat.postMessage", channel="C1", text="x")
    assert err.value.error == "ratelimited" and err.value.retry_after == 3

    def down(*a, **k):
        raise httpc.TransportError("refused")
    with pytest.raises(slackapi.SlackApiError) as err:
        slackapi.SlackApi(TOKEN, request=down).call("auth.test")
    assert err.value.error == "unreachable"


def test_a_server_error_and_a_body_that_is_not_json_are_clear_errors():
    for resp, code in ((httpc.Response(500, {}, b""), "http_500"), (httpc.Response(200, {}, b"<html>"), "not_json")):
        with pytest.raises(slackapi.SlackApiError) as err:
            slackapi.SlackApi(TOKEN, request=lambda *a, **k: resp).call("auth.test")
        assert err.value.error == code


def test_a_post_carries_the_agent_name_and_icon_and_leaves_out_empty_fields():
    client, fake = api()
    out = client.post("C1", "hello", username="Research", icon_emoji=":mag:")
    assert out["ts"].startswith("1700000000.") and fake.params("chat.postMessage") == [
        {"channel": "C1", "text": "hello", "unfurl_links": False, "unfurl_media": False, "username": "Research", "icon_emoji": ":mag:"}]


def test_posts_to_one_channel_wait_a_second_and_other_channels_do_not_wait():
    sleeps, now = [], [100.0]
    client, _ = api(clock=lambda: now[0], sleeps=sleeps)
    client.post("C1", "a")
    now[0] += 0.4
    client.post("C1", "b")
    assert sleeps == [pytest.approx(0.6)]
    client.post("C2", "c")
    assert len(sleeps) == 1
    now[0] += 5
    client.post("C1", "d")
    assert len(sleeps) == 1


def test_the_wait_is_never_longer_than_the_cap():
    sleeps = []
    client, _ = api(sleeps=sleeps, min_interval=60.0)
    client.post("C1", "a")
    client.post("C1", "b")
    assert sleeps == [slackapi.MAX_THROTTLE]


def test_channels_are_listed_and_a_name_that_exists_returns_its_id():
    client, fake = api(FakeSlack(channels={"hq": "C0AAAA"}))
    assert client.channels() == {"hq": "C0AAAA"}
    new = client.create_channel("brief")
    assert new == fake.channels["brief"] and fake.params("conversations.create") == [{"name": "brief", "is_private": False}]
    fake.fail["conversations.create"] = "name_taken"
    assert client.create_channel("hq") == "C0AAAA"
    fake.fail["conversations.create"] = "restricted_action"
    with pytest.raises(slackapi.SlackApiError):
        client.create_channel("zzz")


def test_topic_pin_bookmark_invite_join_and_home_use_the_right_methods():
    client, fake = api()
    client.set_topic("C1", "t" * 400)
    client.pin("C1", "1.2")
    client.bookmark("C1", "n8n", "http://127.0.0.1:5678")
    client.join("C1")
    client.publish_home("U1", {"type": "home", "blocks": []})
    client.update("C1", "1.2", "new", blocks=[{"type": "divider"}])
    assert fake.methods() == ["conversations.setTopic", "pins.add", "bookmarks.add", "conversations.join", "views.publish", "chat.update"]
    assert len(fake.params("conversations.setTopic")[0]["topic"]) == 250
    assert fake.params("bookmarks.add")[0] == {"channel_id": "C1", "title": "n8n", "type": "link", "link": "http://127.0.0.1:5678"}


def test_inviting_a_user_who_is_already_in_the_channel_is_fine_and_other_errors_are_not():
    client, fake = api(FakeSlack(fail={"conversations.invite": "already_in_channel"}))
    assert client.invite("C1", ["U1", "U2"]) == {"ok": True} and fake.params("conversations.invite")[0]["users"] == "U1,U2"
    fake.fail["conversations.invite"] = "user_not_found"
    with pytest.raises(slackapi.SlackApiError):
        client.invite("C1", ["U9"])
