"""Read and write secret files by the rules in config/secrets.yaml.

Rules:
- No function here ever returns a secret value in a message, a log, or an exception.
- A secret file holds lines of NAME=value. The file has mode 600.
- The owner of the file is the user of the one service that reads it.
"""
import glob
import os
import pathlib
import re

import yaml

from . import jsonio, paths, redact


class SecretError(ValueError):
    pass


def registry():
    return yaml.safe_load(paths.secrets_registry().read_text())


def all_secrets():
    return registry()["secrets"]


def get_secret(name):
    for entry in all_secrets():
        if entry["name"] == name:
            return entry
    raise SecretError("unknown secret %s. Run: fleetctl secret list" % name)


def resolve_path(file):
    """Map the path in the registry to a real path. Tests move the roots."""
    file = str(file)
    if file.startswith("/etc/agent-fleet/secrets/"):
        return paths.secrets_dir() / file[len("/etc/agent-fleet/secrets/"):]
    if file.startswith("/home/fleet/.hermes/"):
        return paths.hermes_home() / file[len("/home/fleet/.hermes/"):]
    return pathlib.Path(file)


def secret_path(name):
    return resolve_path(get_secret(name)["file"])


_SAFE = re.compile(r"^[A-Za-z0-9._~+/=@:!%^*()\-]+$")


def quote(value):
    """Write the value so that dotenv parsers and systemd read it the same way."""
    if _SAFE.match(value):
        return value
    if "'" in value:
        raise SecretError("the value has a single quote. Choose a value without one.")
    return "'" + value + "'"


def parse_env(text):
    values = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


def read_values(path):
    try:
        return parse_env(pathlib.Path(path).read_text())
    except (FileNotFoundError, PermissionError):
        return {}


def value_of(name):
    """The secret value, for the one service that owns it. Registers it for redaction."""
    value = read_values(secret_path(name)).get(name, "")
    if value:
        redact.register_secret(value)
    return value


def is_set(name):
    """True when the file holds a non-empty value. None when the file cannot be read."""
    path = secret_path(name)
    try:
        text = path.read_text()
    except FileNotFoundError:
        return False
    except PermissionError:
        return None
    return bool(parse_env(text).get(name))


def check_format(name, value):
    entry = get_secret(name)
    if not value or value != value.strip():
        return False, "the value is empty or has spaces at the ends"
    if "\n" in value or "\r" in value or "\x00" in value:
        return False, "the value has a line break"
    pattern = entry.get("format")
    if pattern and not re.match(pattern, value):
        return False, "the value does not match the format for %s (%s)" % (name, entry["purpose"])
    return True, "ok"


def _update_file(path, name, new_line, owner):
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        lines = []
    replaced = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(name + "=") or stripped.startswith("export " + name + "="):
            lines[i] = new_line
            replaced = True
            break
    if not replaced:
        lines.append(new_line)
    owner = owner if os.geteuid() == 0 else None
    jsonio.atomic_write(path, "\n".join(lines) + "\n", mode=0o600, owner=owner, group=owner)


def also_paths(entry):
    """Extra files that must hold the same value. Only files that exist are returned."""
    found = []
    for pattern in entry.get("also_files", []) or []:
        resolved = resolve_path(pattern)
        found += [pathlib.Path(p) for p in sorted(glob.glob(str(resolved)))]
    return found


def set_env_line(path, name, value, owner):
    """Write one NAME=value line into a file that holds environment lines. Other lines stay. The value is not a secret check."""
    if "\n" in str(value) or "\r" in str(value):
        raise SecretError("the value has a line break")
    _update_file(pathlib.Path(path), name, "%s=%s" % (name, quote(str(value))), owner)


def set_value(name, value, enforce_format=True):
    """Write NAME=value to the right file. Returns the file path."""
    entry = get_secret(name)
    if enforce_format:
        ok, message = check_format(name, value)
        if not ok:
            raise SecretError(message)
    elif "\n" in value or "\r" in value:
        raise SecretError("the value has a line break")
    path = resolve_path(entry["file"])
    if not path.parent.exists():
        raise SecretError("the folder %s does not exist. Run the installer first." % path.parent)
    new_line = "%s=%s" % (name, quote(value))
    _update_file(path, name, new_line, entry["owner"])
    for extra in also_paths(entry):
        if extra != path:
            _update_file(extra, name, new_line, entry["owner"])
    alias = entry.get("alias")
    if alias:                                          # the same value under the name that the service reads. See D-052.
        _update_file(path, alias, "%s=%s" % (alias, quote(value)), entry["owner"])
    redact.register_secret(value)
    return path


def unset_value(name):
    entry = get_secret(name)
    path = resolve_path(entry["file"])
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return False
    keep = [l for l in lines if not l.strip().startswith(name + "=")]
    if len(keep) == len(lines):
        return False
    owner = entry["owner"] if os.geteuid() == 0 else None
    jsonio.atomic_write(path, "\n".join(keep) + ("\n" if keep else ""), mode=0o600, owner=owner, group=owner)
    return True


def status_rows(group=None):
    rows = []
    for entry in all_secrets():
        if group and entry["group"] != group:
            continue
        rows.append({
            "name": entry["name"],
            "group": entry["group"],
            "service": entry["service"],
            "file": entry["file"],
            "set": is_set(entry["name"]),
            "auto": bool(entry.get("auto")),
        })
    return rows
