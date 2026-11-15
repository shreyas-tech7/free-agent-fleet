"""Local tokens that agents present to the Publisher and the Deployer.

The agent side holds the token. The service holds only its SHA-256 hash, in its own secret file
under NAME_SHA256. A leak of the service file does not give an attacker a token.
"""
import hashlib
import os
import secrets as pysecrets

from . import httpd, secrets

# token name -> (service secret file, hash variable name)
DERIVED = {
    "PUBLISHER_API_TOKEN": ("/etc/agent-fleet/secrets/publisher.env", "PUBLISHER_API_TOKEN_SHA256", "fleetops"),
    "PUBLISHER_REPLY_TOKEN": ("/etc/agent-fleet/secrets/publisher.env", "PUBLISHER_REPLY_TOKEN_SHA256", "fleetops"),
    "DEPLOYER_API_TOKEN": ("/etc/agent-fleet/secrets/deployer.env", "DEPLOYER_API_TOKEN_SHA256", "fleetops"),
    "CONTROL_NOTIFY_TOKEN": ("/etc/agent-fleet/secrets/control.env", "CONTROL_NOTIFY_TOKEN_SHA256", "fleetops"),
    "FLEET_ACTION_TOKEN": ("/etc/agent-fleet/secrets/control.env", "FLEET_ACTION_TOKEN_SHA256", "fleetops"),
}


def rotate(name):
    """Make a new token. Store its hash for the service, and then store the token for the agent.

    The hash goes first. If the second write fails, the service holds the hash of a token that nobody has. That is harmless,
    and the next run makes a new token. The other order could leave a token that the service refuses. See D-053.
    """
    if name not in DERIVED:
        raise secrets.SecretError("%s is not a local token" % name)
    file, hash_var, owner = DERIVED[name]
    path = secrets.resolve_path(file)
    if not path.parent.exists():
        raise secrets.SecretError("the folder %s does not exist" % path.parent)
    token = pysecrets.token_urlsafe(36)
    secrets._update_file(path, hash_var, "%s=%s" % (hash_var, httpd.token_hash(token)), owner)
    secrets.set_value(name, token)
    return True


def hashes(name):
    """The hashes that the service accepts for a token name, as the file holds them now.

    A service that bind-mounts the file sees the copy that was there when it started. A new hash needs a restart. See D-053.
    """
    file, hash_var, _owner = DERIVED[name]
    value = secrets.read_values(secrets.resolve_path(file)).get(hash_var, "")
    return [h for h in value.split(",") if len(h) == 64]


def matches(name):
    """True when the token that the agent holds has its hash in the service file."""
    token = secrets.value_of(name)
    return bool(token) and httpd.token_hash(token) in hashes(name)


def repair(name):
    """The token exists but the service file has no matching hash. Write the hash again. The token stays the same.

    Returns True when it changed the file. It never makes a new token, so a credential that holds the token stays valid.
    """
    if name not in DERIVED:
        raise secrets.SecretError("%s is not a local token" % name)
    token = secrets.value_of(name)
    file, hash_var, owner = DERIVED[name]
    path = secrets.resolve_path(file)
    if not token or not path.parent.exists() or matches(name):
        return False
    secrets._update_file(path, hash_var, "%s=%s" % (hash_var, httpd.token_hash(token)), owner)
    return True
