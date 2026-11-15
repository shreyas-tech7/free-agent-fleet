import json
import urllib.request

from tests.mock_llm import MockLLM, SCENARIOS


def post(mock, body):
    req = urllib.request.Request(mock.url + "/chat/completions", method="POST",
                                 data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.read().decode()


def test_models_and_plain_answer():
    with MockLLM() as mock:
        with urllib.request.urlopen(mock.url + "/models", timeout=5) as r:
            ids = [m["id"] for m in json.loads(r.read())["data"]]
        assert {"fleet-smart", "fleet-worker", "fleet-coder", "fleet-local"} <= set(ids)
        out = json.loads(post(mock, {"model": "fleet-worker", "messages": [{"role": "user", "content": "hello there"}]}))
        assert out["choices"][0]["message"]["content"].startswith("MOCK-OK")
        assert len(mock.requests) == 1


def test_scenarios_return_schema_valid_json():
    with MockLLM() as mock:
        for name in SCENARIOS:
            out = json.loads(post(mock, {"model": "fleet-smart",
                                         "messages": [{"role": "user", "content": "SCENARIO:" + name}]}))
            assert json.loads(out["choices"][0]["message"]["content"]) == SCENARIOS[name]


def test_tool_call_marker_asks_for_the_tool_once():
    tools = [{"type": "function", "function": {"name": "terminal", "parameters": {"type": "object"}}}]
    with MockLLM() as mock:
        first = json.loads(post(mock, {"messages": [{"role": "user", "content": "CALL_TOOL:terminal"}], "tools": tools}))
        assert first["choices"][0]["finish_reason"] == "tool_calls"
        msgs = [{"role": "user", "content": "CALL_TOOL:terminal"},
                first["choices"][0]["message"],
                {"role": "tool", "tool_call_id": "x", "content": "ok"}]
        second = json.loads(post(mock, {"messages": msgs, "tools": tools}))
        assert second["choices"][0]["finish_reason"] == "stop"


def test_streaming_ends_with_done():
    with MockLLM() as mock:
        body = post(mock, {"stream": True, "messages": [{"role": "user", "content": "hi"}]})
        assert body.strip().endswith("data: [DONE]")
        assert "MOCK-OK" in body
