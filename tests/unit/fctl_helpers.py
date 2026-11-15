"""Shared fakes for the fleetctl tests."""
import io
import json

from ops.fleetctl import ui
from ops.fleetlib import httpc


class TTY(io.StringIO):
    def isatty(self):
        return True


class Script:
    """A console with scripted answers. `inputs` feed ask and confirm. `hidden` feeds ask_secret.

    `tty_out` makes the output look like a terminal. Only `secret show` needs that."""

    def __init__(self, inputs=(), hidden=(), interactive=True, tty_out=False):
        self.inputs, self.hidden = list(inputs), list(hidden)
        self.out = TTY() if tty_out else io.StringIO()

        def _input(prompt):
            if not self.inputs:
                raise EOFError
            return self.inputs.pop(0)

        def _hidden(prompt):
            if not self.hidden:
                raise EOFError
            return self.hidden.pop(0)

        stdin = TTY() if interactive else io.StringIO()
        self.con = ui.Console(stdin=stdin, stdout=self.out, getpass_fn=_hidden, input_fn=_input)

    @property
    def text(self):
        return self.out.getvalue()


class Runner:
    """A fake command runner. `rules` is a list of (words, (rc, output)). The first rule whose words all appear in argv wins."""

    def __init__(self, rules=None, default=(0, "")):
        self.calls = []
        self.inputs = []             # what each call got on standard input
        self.rules = list(rules or [])
        self.default = default

    def __call__(self, argv, timeout=60, user=None, extra_env=None, input_text=None):
        argv = list(argv)
        self.calls.append((argv, user))
        self.inputs.append(input_text)
        for words, result in self.rules:
            if all(w in argv for w in words):
                return result
        return self.default

    def ran(self, *words):
        return any(all(w in argv for w in words) for argv, _u in self.calls)


class FakeHttp:
    """Stands in for ops.fleetlib.httpc. Answers by URL prefix."""

    def __init__(self, answers=None, default=(200, {})):
        self.calls = []
        self.answers = answers or {}
        self.default = default

    def request(self, method, url, headers=None, json_body=None, timeout=20, **kw):
        self.calls.append({"method": method, "url": url, "headers": headers or {}, "json": json_body})
        for prefix, answer in self.answers.items():
            if url.startswith(prefix):
                if answer is None:
                    raise httpc.TransportError("down")
                status, body = answer
                return httpc.Response(status, {}, json.dumps(body).encode())
        status, body = self.default
        return httpc.Response(status, {}, json.dumps(body).encode())
