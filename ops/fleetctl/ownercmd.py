"""fleetctl owner. The private owner file. See D-046.

  check   validate the file. It names each problem and never prints a value.
  apply   copy config/owner.yaml of the repository to /etc/agent-fleet/owner.yaml after it validates
"""
import os
import pathlib
import shutil

from ops.fleetlib import owner, paths


def _source(args):
    return pathlib.Path(getattr(args, "file", None) or paths.repo_root() / "config" / "owner.yaml")


def cmd_check(args, console, runner=None):
    path = pathlib.Path(args.file) if getattr(args, "file", None) else paths.owner_config()
    try:
        data = owner.read(path)
    except owner.OwnerError as exc:
        console.say("Not valid: %s" % exc)
        return 1
    if not data:
        console.say("There is no owner file at %s. Copy templates/owner.example.yaml to config/owner.yaml, edit it, and run: fleetctl owner apply" % path)
        return 1
    problems = owner.validate(data)
    for problem in problems:
        console.say("Problem: %s" % problem)
    console.say("The owner file is %s." % ("not valid" if problems else "valid"))
    return 1 if problems else 0


def cmd_apply(args, console, runner=None):
    source = _source(args)
    try:
        data = owner.read(source)
    except owner.OwnerError as exc:
        console.say("Not applied: %s" % exc)
        return 1
    if not data:
        console.say("Not applied: there is no file at %s." % source)
        return 1
    problems = owner.validate(data)
    if problems:
        for problem in problems:
            console.say("Problem: %s" % problem)
        console.say("Not applied. Fix the file first.")
        return 1
    target = paths.owner_config()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".new")
    shutil.copyfile(str(source), str(tmp))
    os.chmod(str(tmp), 0o644)
    os.replace(str(tmp), str(target))
    console.say("Applied. The live owner file is %s. Services read it when they start." % target)
    return 0


def dispatch(args, console, runner=None):
    table = {"check": cmd_check, "apply": cmd_apply}
    fn = table.get(args.owner_command)
    if not fn:
        console.say("Choose one of: %s" % ", ".join(sorted(table)))
        return 2
    return fn(args, console, runner)
