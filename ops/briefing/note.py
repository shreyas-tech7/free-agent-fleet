"""A one-line note for the next brief. See D-061.

The owner, or a build step, writes it with `fleetctl brief --note "TEXT"`. The next brief puts it first in the part Achieved.
The brief sends it once. After a delivered brief the note is removed. A brief that did not reach anybody keeps it for the next try.
A note has 20 words or fewer. A value that looks like a secret is hidden before the note is stored.
"""
import os
import pathlib
import re
import shutil

from ops.fleetlib import housestyle, jsonio, paths, redact

MAX_WORDS = 20


class NoteError(ValueError):
    pass


def note_path():
    return pathlib.Path(paths.ops_state_dir()) / "briefing" / "NOTE"


def clean(text):
    """The note as it will be stored. Raises NoteError when it is empty or too long."""
    one_line = " ".join(str(text or "").split())
    if not one_line:
        raise NoteError("The note is empty.")
    one_line = housestyle.fix(redact.redact_text(one_line)).strip()
    count = len(one_line.split())
    if count > MAX_WORDS:
        raise NoteError("The note has %d words. The limit is %d." % (count, MAX_WORDS))
    return one_line


def write(text):
    value = clean(text)
    owner = "fleetops" if os.geteuid() == 0 else None
    target = note_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    if owner:
        shutil.chown(target.parent, owner, owner)
    jsonio.atomic_write(target, value + "\n", mode=0o640, owner=owner, group=owner)
    return value


def read():
    try:
        return " ".join(note_path().read_text().split())[:400]
    except OSError:
        return ""


def clear():
    try:
        note_path().unlink()
    except FileNotFoundError:
        pass
