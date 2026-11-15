"""Terminal input and output for fleetctl. Tests replace the Console with a scripted one."""
import getpass
import os
import shutil
import sys


class Console:
    def __init__(self, stdin=None, stdout=None, getpass_fn=None, input_fn=None):
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self._getpass = getpass_fn or getpass.getpass
        self._input = input_fn or input

    def say(self, text=""):
        print(text, file=self.stdout, flush=True)

    def ask(self, prompt, default=None):
        """Ask one question. An empty answer gives the default."""
        suffix = " [%s]" % default if default not in (None, "") else ""
        try:
            answer = self._input("%s%s: " % (prompt, suffix)).strip()
        except EOFError:
            answer = ""
        return answer or (default if default is not None else "")

    def ask_secret(self, prompt):
        """Ask for a secret. The terminal does not show the typing. The value is never echoed."""
        try:
            return self._getpass(prompt)
        except EOFError:
            return ""

    def confirm(self, prompt, default=False):
        hint = "Y/n" if default else "y/N"
        answer = self.ask("%s (%s)" % (prompt, hint)).lower()
        if not answer:
            return default
        return answer in ("y", "yes")

    @property
    def interactive(self):
        try:
            return bool(self.stdin.isatty())
        except (AttributeError, ValueError):
            return False

    @property
    def output_is_tty(self):
        try:
            return bool(self.stdout.isatty())
        except (AttributeError, ValueError):
            return False

    def erase_lines(self, count):
        """Move the cursor up and clear to the end of the screen. Used to hide a shown secret."""
        if not self.output_is_tty or os.environ.get("TERM", "dumb") == "dumb":
            return
        self.stdout.write("\x1b[%dA\x1b[0J" % count)
        self.stdout.flush()

    def columns(self):
        return max(20, shutil.get_terminal_size((80, 24)).columns)
