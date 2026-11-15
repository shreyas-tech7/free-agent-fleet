#!/usr/bin/env python3
"""A mock model server. It speaks the OpenAI chat completions API.

It is for tests and for the dry run. It never calls a real model, so it costs nothing.
It returns canned answers that match the schemas of the fleet. It logs each request.

Run it:      python3 tests/mock_llm.py --port 20129 --log /tmp/mock-llm.jsonl
In a test:   with MockLLM() as mock: ... mock.url ... mock.requests

Answers:
- A user message that holds  CALL_TOOL:<name> ARGS:<json>  makes the mock ask for that tool once.
  The ARGS part is optional.
- A user message that holds  SCENARIO:<name>  returns the canned JSON in SCENARIOS.
- Anything else returns the text "MOCK-OK" and a short echo of the last user message.
"""
import argparse
import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODELS = ["fleet-smart", "fleet-worker", "fleet-coder", "fleet-local", "fleet-mock"]

# Canned, schema-valid answers for the dry run. Keys match the SCENARIO:<name> marker.
SCENARIOS = {
    "pain_report": {
        "project": "dry-run-widgets",
        "candidates": [
            {"id": "c1", "pain": "Small shops lose track of widget orders", "complaints": 12,
             "paid_workaround": "spreadsheet templates sold for $9",
             "scores": {"pain": 4, "proof_of_paying": 3, "free_channel_reach": 4,
                        "buildable_in_two_weeks": 5, "competitor_gaps": 3, "legal_risk": 5}},
            {"id": "c2", "pain": "Hobby sellers cannot price widgets", "complaints": 10,
             "paid_workaround": "paid pricing course",
             "scores": {"pain": 3, "proof_of_paying": 3, "free_channel_reach": 3,
                        "buildable_in_two_weeks": 4, "competitor_gaps": 2, "legal_risk": 5}},
            {"id": "c3", "pain": "Makers forget to reorder parts", "complaints": 11,
             "paid_workaround": "inventory app subscription",
             "scores": {"pain": 3, "proof_of_paying": 4, "free_channel_reach": 2,
                        "buildable_in_two_weeks": 4, "competitor_gaps": 3, "legal_risk": 5}},
        ],
    },
    "ticket": {
        "category": "bug", "severity": "medium",
        "summary": "The export button does nothing on small screens.",
        "evidence_link": "projects/dry-run-widgets/feedback/f-001.json", "user_hash": "u_3f9a1c2b7d4e",
    },
    "brief": {
        "achieved": "Ran the dry-run project through every stage.",
        "next": "Wait for owner keys. Then start the Discover stage on live models.",
        "limits": "No model keys yet. No payment account yet.",
    },
    "decision": {"decision": "continue", "reason": "All gate numbers are met in the mock data."},
    "landing": {
        "headline": "Keep every widget order in one place",
        "body": "Small shops lose track of widget orders in spreadsheets. Widget Tracker keeps each order, its status, and its due date on one page.",
        "cta": "Join the waitlist",
        "faq": [{"id": "pricing_free_tier", "question": "Is there a free plan?", "answer": "Yes. The free plan covers up to 20 orders."}],
    },
    "post_validate": {"text": "We are building a small tool that keeps widget orders in one place. The waitlist is open. Tell us what your orders look like."},
    "post_launch": {"text": "Widget Tracker is open. It keeps every widget order, status, and due date on one page. Try it and tell us what is missing."},
    "fix_note": {"summary": "Made the export button work on small screens and added a test for it.", "tests_pass": True},
    "memo": {"numbers": "23 waitlist signups. 12 active users. 0 payments.", "next": "Wait for the owner to set up payments."},
}


def build_completion(body, counter):
    messages = body.get("messages", [])
    last_user = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content")
            last_user = content if isinstance(content, str) else json.dumps(content)
            break
    has_tool_result = any(m.get("role") == "tool" for m in messages)
    tools = body.get("tools") or []
    wanted = re.search(r"CALL_TOOL:([A-Za-z0-9_\-]+)(?:\s+ARGS:(\{.*\}))?", last_user, re.S)
    scenario = re.search(r"SCENARIO:([A-Za-z0-9_\-]+)", last_user)
    message = {"role": "assistant", "content": None}
    finish = "stop"
    if wanted and tools and not has_tool_result:
        names = {t.get("function", {}).get("name") for t in tools}
        name = wanted.group(1)
        if name in names:
            message["tool_calls"] = [{
                "id": "call_%d" % counter, "type": "function",
                "function": {"name": name, "arguments": wanted.group(2) or "{}"},
            }]
            finish = "tool_calls"
    if finish == "stop":
        if scenario and scenario.group(1) in SCENARIOS:
            message["content"] = json.dumps(SCENARIOS[scenario.group(1)])
        else:
            echo = re.sub(r"\s+", " ", last_user)[:80]
            message["content"] = "MOCK-OK. You said: " + echo
    return message, finish


class Handler(BaseHTTPRequestHandler):
    server_version = "mock-llm"

    def log_message(self, *args):
        return

    def _json(self, status, obj):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            self._json(200, {"object": "list", "data": [{"id": m, "object": "model", "owned_by": "mock"} for m in MODELS]})
        elif self.path.endswith("/health"):
            self._json(200, {"ok": True})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode() or "{}")
        except ValueError:
            return self._json(400, {"error": "bad json"})
        if not self.path.rstrip("/").endswith("/chat/completions"):
            return self._json(404, {"error": "not found"})
        srv = self.server
        with srv.lock:
            srv.counter += 1
            n = srv.counter
        record = {"n": n, "ts": time.time(), "model": body.get("model"), "stream": bool(body.get("stream")),
                  "tools": len(body.get("tools") or []), "messages": len(body.get("messages", [])),
                  "last_user": next((m.get("content") for m in reversed(body.get("messages", [])) if m.get("role") == "user"), None),
                  "last_tool": next((str(m.get("content"))[:600] for m in reversed(body.get("messages", [])) if m.get("role") == "tool"), None)}
        srv.requests.append(record)
        if srv.logfile:
            with open(srv.logfile, "a") as fh:
                fh.write(json.dumps(record, default=str) + "\n")
        message, finish = build_completion(body, n)
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        base = {"id": "mock-%d" % n, "created": int(time.time()), "model": body.get("model") or "fleet-mock"}
        if not body.get("stream"):
            return self._json(200, dict(base, object="chat.completion", usage=usage,
                                        choices=[{"index": 0, "message": message, "finish_reason": finish}]))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def send(delta, fin=None, with_usage=False):
            chunk = dict(base, object="chat.completion.chunk",
                         choices=[{"index": 0, "delta": delta, "finish_reason": fin}])
            if with_usage:
                chunk["usage"] = usage
            self.wfile.write(("data: %s\n\n" % json.dumps(chunk)).encode())
            self.wfile.flush()

        send({"role": "assistant", "content": ""})
        if message.get("tool_calls"):
            tc = message["tool_calls"][0]
            send({"tool_calls": [{"index": 0, "id": tc["id"], "type": "function",
                                  "function": {"name": tc["function"]["name"], "arguments": ""}}]})
            send({"tool_calls": [{"index": 0, "function": {"arguments": tc["function"]["arguments"]}}]})
        elif message.get("content"):
            text = message["content"]
            for i in range(0, len(text), 24):
                send({"content": text[i:i + 24]})
        send({}, fin=finish, with_usage=True)
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


class MockLLM:
    """Start and stop the mock in a test."""

    def __init__(self, port=0, logfile=None):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.httpd.counter = 0
        self.httpd.lock = threading.Lock()
        self.httpd.requests = []
        self.httpd.logfile = logfile
        self.port = self.httpd.server_address[1]
        self.url = "http://127.0.0.1:%d/v1" % self.port
        self._thread = None

    @property
    def requests(self):
        return self.httpd.requests

    def __enter__(self):
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=20129)
    ap.add_argument("--log", default=None)
    args = ap.parse_args(argv)
    mock = MockLLM(args.port, args.log)
    print("mock model on %s" % mock.url, flush=True)
    try:
        mock.httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
