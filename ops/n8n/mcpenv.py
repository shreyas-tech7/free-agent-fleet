"""fleetctl n8n mcp-env. Write the env file for the n8n-mcp helper of Claude Code.

n8n-mcp is for Claude Code only. Fleet agents never get it. The file holds the n8n address and the API key.
It goes to ~/.config/n8n-mcp/env of the person who ran sudo, with mode 600 and that person as the owner.
The key never goes to a log, to git, or to a chat. See D-040.
"""
import os
import pathlib
import pwd

from ops.fleetlib import config, jsonio, secrets


class McpEnvError(Exception):
    pass


def target_path(user=None):
    user = user or os.environ.get("SUDO_USER") or ""
    if not user or user == "root":
        raise McpEnvError("run this with sudo from the owner account, so that the file goes to the owner home")
    try:
        info = pwd.getpwnam(user)
    except KeyError:
        raise McpEnvError("no such user: %s" % user) from None
    return pathlib.Path(info.pw_dir) / ".config" / "n8n-mcp" / "env", info


def write(cfg=None, user=None):
    cfg = cfg or config.load()
    key = secrets.value_of("N8N_API_KEY")
    if not key:
        raise McpEnvError("there is no API key yet. Run: fleetctl n8n bootstrap")
    path, info = target_path(user)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "N8N_API_URL=http://127.0.0.1:%d\nN8N_API_KEY=%s\n" % (config.port(cfg, "n8n"), key)
    owner = info.pw_name if os.geteuid() == 0 else None
    jsonio.atomic_write(path, text, mode=0o600, owner=owner, group=owner)
    if os.geteuid() == 0:
        os.chmod(path.parent, 0o700)
        os.chown(path.parent, info.pw_uid, info.pw_gid)
    return path
