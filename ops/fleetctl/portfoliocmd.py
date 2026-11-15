"""fleetctl portfolio sync. Copy the weekly log into docs/PORTFOLIO_LOG.md of the private repository. See D-048.

The weekly job writes /var/lib/agent-fleet-ops/portfolio/PORTFOLIO_LOG.md. That folder is not in git.
This command appends the weeks that the repository file does not have yet. The owner then commits the file.
"""
import os
import pathlib
import pwd
import re

from ops.briefing import weekly

HEAD = re.compile(r"^## Week ending (\d{4}-\d{2}-\d{2})\s*$", re.M)
HEADER = "# Portfolio log\n\nWhat the fleet shipped each week. Newest entries come last.\n"


def sections(text):
    """{date: section text} in file order."""
    found = list(HEAD.finditer(text))
    out = {}
    for i, m in enumerate(found):
        end = found[i + 1].start() if i + 1 < len(found) else len(text)
        out[m.group(1)] = text[m.start():end].strip()
    return out


def merge(source_text, target_text):
    """Return (new target text, number of weeks added)."""
    have = sections(target_text)
    add = [(d, s) for d, s in sections(source_text).items() if d not in have]
    base = (target_text or HEADER).rstrip("\n")
    for _date, section in add:
        base += "\n\n" + section
    return base + "\n", len(add)


def default_repo():
    user = os.environ.get("SUDO_USER")
    if user and user != "root":
        return pathlib.Path(pwd.getpwnam(user).pw_dir) / "agent-fleet"
    return pathlib.Path.cwd()


def cmd_sync(args, console, runner=None):
    source = weekly.log_file()
    if not source.exists():
        console.say("There is no portfolio log yet. The weekly job writes it on Sunday at 18:00.")
        return 1
    repo = pathlib.Path(args.repo) if getattr(args, "repo", None) else default_repo()
    target = repo / "docs" / "PORTFOLIO_LOG.md"
    if not target.parent.is_dir():
        console.say("%s is not a repository folder. Pass --repo PATH." % repo)
        return 1
    text, added = merge(source.read_text(), target.read_text() if target.exists() else "")
    if added:
        target.write_text(text)
        if os.geteuid() == 0:
            info = target.parent.stat()
            os.chown(str(target), info.st_uid, info.st_gid)
    console.say("Added %d week%s to %s." % (added, "" if added == 1 else "s", target) if added else "The repository file is up to date.")
    return 0


def dispatch(args, console, runner=None):
    if args.portfolio_command == "sync":
        return cmd_sync(args, console, runner)
    console.say("Choose: sync")
    return 2
