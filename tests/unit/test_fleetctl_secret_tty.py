"""Real terminal tests for `fleetctl secret set` and `fleetctl secret show`.

They use a pseudo terminal, so getpass and isatty behave as they do over SSH.
The value must never appear in the terminal output of `set`. `show` must refuse a pipe.
"""
import os
import pathlib
import pty
import select
import stat
import subprocess
import sys
import time

from ops.fleetlib import secrets

ROOT = pathlib.Path(__file__).resolve().parents[2]
FLEETCTL = str(ROOT / "fleetctl")
GROQ = "gsk_" + "Zy9Xw8Vu7T" * 3


def _env():
    env = dict(os.environ)
    env["TERM"] = "xterm"
    return env


def _run_in_pty(argv, steps, timeout=25):
    """Run fleetctl in a pseudo terminal. `steps` is a list of (text to wait for, text to send)."""
    pid, fd = pty.fork()
    if pid == 0:
        os.execve(sys.executable, [sys.executable, "-I", FLEETCTL] + argv, _env())  # nosemgrep: dangerous-os-exec-audit
    out = b""
    steps = list(steps)
    deadline = time.time() + timeout
    while time.time() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.2)
        if not ready:
            continue
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            break
        if not chunk:
            break
        out += chunk
        if steps and steps[0][0].encode() in out:
            os.write(fd, steps.pop(0)[1].encode())
    _, status = os.waitpid(pid, 0)
    return out.decode(errors="replace"), os.waitstatus_to_exitcode(status)


def test_set_on_a_real_terminal_hides_the_typing_and_writes_mode_600(fleet_env):
    out, code = _run_in_pty(["secret", "set", "GROQ_API_KEY"], [("Value for GROQ_API_KEY", GROQ + "\n")])
    assert code == 0, out
    assert GROQ not in out, "the typed value was echoed to the terminal"
    assert "Saved GROQ_API_KEY" in out
    path = secrets.secret_path("GROQ_API_KEY")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert secrets.value_of("GROQ_API_KEY") == GROQ


def test_set_with_stdin_flag_on_a_real_terminal_is_refused(fleet_env):
    out, code = _run_in_pty(["secret", "set", "GROQ_API_KEY", "--stdin"], [])
    assert code == 2, out
    assert "would show" in out
    assert not secrets.is_set("GROQ_API_KEY")


def test_set_through_a_pipe_needs_the_stdin_flag(fleet_env):
    base = [sys.executable, "-I", FLEETCTL, "secret", "set", "GROQ_API_KEY"]
    refused = subprocess.run(base, input=GROQ + "\n", capture_output=True, text=True, env=_env(), timeout=25)  # nosemgrep: dangerous-subprocess-use-audit
    assert refused.returncode == 2 and GROQ not in refused.stdout + refused.stderr
    ok = subprocess.run(base + ["--stdin"], input=GROQ + "\n", capture_output=True, text=True, env=_env(), timeout=25)  # nosemgrep: dangerous-subprocess-use-audit
    assert ok.returncode == 0 and GROQ not in ok.stdout + ok.stderr
    assert stat.S_IMODE(secrets.secret_path("GROQ_API_KEY").stat().st_mode) == 0o600


def test_show_on_a_real_terminal_prints_then_hides(fleet_env):
    secrets.set_value("GROQ_API_KEY", GROQ)
    out, code = _run_in_pty(["secret", "show", "GROQ_API_KEY"], [("Press Enter to hide it", "\n")])
    assert code == 0, out
    assert GROQ in out
    assert "\x1b[0J" in out     # the value is erased after Enter


def test_show_is_refused_when_output_goes_to_a_pipe(fleet_env):
    secrets.set_value("GROQ_API_KEY", GROQ)
    master, slave = pty.openpty()
    try:
        done = subprocess.run([sys.executable, "-I", FLEETCTL, "secret", "show", "GROQ_API_KEY"], stdin=slave,  # nosemgrep: dangerous-subprocess-use-audit
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=_env(), timeout=25)
    finally:
        os.close(master)
        os.close(slave)
    assert done.returncode == 2
    assert GROQ not in done.stdout.decode()


def test_show_is_refused_when_there_is_no_terminal_at_all(fleet_env):
    secrets.set_value("GROQ_API_KEY", GROQ)
    done = subprocess.run([sys.executable, "-I", FLEETCTL, "secret", "show", "GROQ_API_KEY"], stdin=subprocess.DEVNULL,  # nosemgrep: dangerous-subprocess-use-audit
                          capture_output=True, text=True, env=_env(), timeout=25)
    assert done.returncode == 2
    assert GROQ not in done.stdout + done.stderr
